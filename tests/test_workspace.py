"""Two-layer sandbox storage: footprint vs snapshot cap, verified slim clone, sparse work area, park, media guard."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import handoff as h  # noqa: E402
import progress_sync as ps  # noqa: E402
import workspace as ws  # noqa: E402


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode().strip()


def bare(base, name):
    r = base / name
    git('init', '--quiet', '--bare', str(r), cwd=base)
    git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=r)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.base = Path(self.t.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {'AGENT_WORKSPACE_HOME': str(self.home),
                                                'AGENT_WORK_ROOT': str(self.home / '.cache' / 'work')})
        self.env.start()
        self.g = ps.Git()

    def tearDown(self):
        self.g.close()
        self.env.stop()
        self.t.cleanup()


class Footprint(Base):
    def test_counts_only_what_the_snapshot_keeps(self):
        (self.home / 'keep.bin').write_bytes(b'x' * 1000)
        for folder, size in (('build', 50_000), ('node_modules/pkg', 40_000), ('.cache/work/p', 80_000)):
            (self.home / folder).mkdir(parents=True)
            (self.home / folder / 'big.bin').write_bytes(b'x' * size)
        (self.home / 'repo' / '.git').mkdir(parents=True)
        (self.home / 'repo' / '.git' / 'config').write_text('c' * 5000)
        (self.home / 'repo' / '.git' / 'HEAD').write_text('ref')
        fp = ws.footprint()
        self.assertEqual((fp['bytes'], fp['files'], fp['work_area_bytes'], fp['level']), (1003, 2, 80_000, 'ok'))
        with mock.patch.object(ws, 'CAP_BYTES', 1400):
            self.assertEqual(ws.footprint()['level'], 'warn')
        with mock.patch.object(ws, 'CAP_BYTES', 1100):
            self.assertEqual(ws.footprint()['level'], 'fail')
        with mock.patch.object(ws, 'CAP_FILES', 2):
            self.assertEqual(ws.footprint()['level'], 'fail')


class SlimAndMedia(Base):
    def setUp(self):
        super().setUp()
        self.remote = bare(self.base, 'progress.git')
        self.url = 'file://' + str(self.remote)
        self.seed = self.base / 'seed'
        ps.clone(self.g, self.seed, url=self.url)
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=self.seed)
        (self.seed / 'LOCATION.json').write_text(json.dumps({'progress_remote_url': self.url}))
        self.cp = h.checkpoint(self.seed, 'test/one', h.new_state('One', 'g', next_actions=['x']), 'NEW', 'create')
        (self.seed / 'evidence').mkdir()
        (self.seed / 'evidence' / 'big.bin').write_bytes(os.urandom(200_000))
        (self.seed / 'sync-receipts').mkdir()
        (self.seed / 'sync-receipts' / 'r.json').write_text('{}')
        ps.push(self.seed, self.g, ['test/one'], ['LOCATION.json', 'evidence', 'sync-receipts'], 'seed')

    def test_slim_is_verified_and_idempotent(self):
        full = self.home / 'agent-progress'
        ps.clone(self.g, full, url=self.url)
        self.assertTrue((full / 'evidence' / 'big.bin').exists())
        res = ws.slim(full)
        self.assertEqual(res['status'], 'SLIMMED')
        self.assertLess(res['after']['bytes'], res['before']['bytes'])
        self.assertFalse((full / 'evidence').exists())
        self.assertFalse((full / 'sync-receipts').exists())
        self.assertTrue(h.validate(full)['pass'])
        self.assertEqual(git('config', '--get', 'remote.origin.promisor', cwd=full), 'true')
        self.assertEqual(ws.slim(full)['status'], 'ALREADY_SLIM')
        self.assertFalse((self.home / 'agent-progress.full-backup').exists())
        git('sparse-checkout', 'add', '/evidence/', cwd=full)  # fetched on demand when really needed
        self.assertTrue((full / 'evidence' / 'big.bin').exists())

    def test_slim_refuses_unpushed_work_and_changes_nothing(self):
        full = self.home / 'agent-progress'
        ps.clone(self.g, full, url=self.url)
        (full / 'LOCATION.json').write_text('{"edited": true}')
        with self.assertRaises(ws.WorkspaceError):
            ws.slim(full)
        self.assertTrue((full / 'evidence' / 'big.bin').exists())
        self.assertEqual((full / 'LOCATION.json').read_text(), '{"edited": true}')

    def test_clone_slim_and_media_guard(self):
        slim = self.home / 'slim'
        self.assertTrue(ps.clone(self.g, slim, url=self.url, slim=True)['slim'])
        self.assertFalse((slim / 'evidence').exists())
        self.assertTrue((slim / 'projects' / 'test' / 'one' / 'HEAD.json').exists())
        clip = self.seed / 'projects' / 'test' / 'one' / 'evidence' / 'clip.mp4'
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b'\x00\x00')
        with self.assertRaises(ps.SyncError) as ctx:
            ps.push(self.seed, self.g, ['test/one'], message='media')
        self.assertIn('audio/video', str(ctx.exception))

    def test_doctor_reports_workspace_level(self):
        prog = self.home / 'agent-progress'
        ps.clone(self.g, prog, url=self.url, slim=True)
        by = {c['check']: c for c in ps.doctor(prog, self.g, token_present=True)['checks']}
        self.assertTrue(by['workspace']['ok'], by['workspace'])
        with mock.patch.object(ws, 'CAP_BYTES', 10_000):
            by = {c['check']: c for c in ps.doctor(prog, self.g, token_present=True)['checks']}
            self.assertFalse(by['workspace']['ok'])
            self.assertNotIn('advisory', by['workspace'])  # over 85%: a real failure, not a hint


class WorkArea(Base):
    def test_open_sparse_update_and_park(self):
        remote = bare(self.base, 'assets.git')
        url = 'file://' + str(remote)
        up = self.base / 'up'
        ps.clone(self.g, up, url=url)
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=up)
        for rel in ('episodes/e1/a.txt', 'episodes/e2/b.txt', 'README.md'):
            (up / rel).parent.mkdir(parents=True, exist_ok=True)
            (up / rel).write_text(rel)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '-A', cwd=up)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init', cwd=up)
        git('push', '-q', 'origin', 'HEAD:main', cwd=up)
        r = ws.open_repo('assets', url=url, paths=['episodes/e1'])
        d = Path(r['dir'])
        self.assertEqual(d, self.home / '.cache' / 'work' / 'assets')
        self.assertTrue((d / 'episodes' / 'e1' / 'a.txt').exists())
        self.assertFalse((d / 'episodes' / 'e2').exists())
        self.assertTrue((d / 'README.md').exists())
        self.assertEqual(ws.open_repo('assets', url=url, paths=['episodes/e2'])['status'], 'UPDATED')
        self.assertTrue((d / 'episodes' / 'e2' / 'b.txt').exists())
        self.assertEqual(ws.park()['unpushed'], [])
        (d / 'episodes' / 'e1' / 'a.txt').write_text('changed')
        self.assertEqual(ws.park()['unpushed'], ['assets'])
        pushed = ws.park(push=True, message='wip')
        self.assertEqual(pushed['repos'][0]['state'], 'PUSHED_VERIFIED')
        self.assertEqual(git('rev-parse', 'refs/heads/main', cwd=remote), pushed['repos'][0]['commit'])
        (self.home / '.cache' / 'work' / 'loose').mkdir()
        (self.home / '.cache' / 'work' / 'loose' / 'x.bin').write_bytes(b'1')
        self.assertIn('loose', ws.park()['unpushed'])  # not a repository: would be lost at restart
        fp = ws.footprint()
        self.assertGreater(fp['work_area_bytes'], 0)
        self.assertEqual(fp['files'], 0)  # nothing in the work area counts toward the snapshot


if __name__ == '__main__':
    unittest.main()
