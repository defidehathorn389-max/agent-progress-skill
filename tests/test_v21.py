"""v2.1: snapshot-resilient sync (origin/identity repair), pull, save, doctor, handoff new."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import handoff as h  # noqa: E402
import progress_sync as ps  # noqa: E402


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode().strip()


def state(title):
    return h.new_state(title, 'Sync safely', next_actions=['continue'])


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        base = Path(self.t.name)
        self.remote = base / 'remote.git'
        git('init', '--quiet', '--bare', str(self.remote), cwd=base)
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=self.remote)
        self.g = ps.Git()
        self.a = base / 'a'
        ps.clone(self.g, self.a, url=str(self.remote))
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=self.a)
        git('config', 'user.name', 'Owner Name', cwd=self.a)
        git('config', 'user.email', 'owner@example.com', cwd=self.a)
        (self.a / 'LOCATION.json').write_text(json.dumps({'progress_remote_url': str(self.remote)}))
        self.ha = h.checkpoint(self.a, 'test/one', state('One'), 'NEW', 'create one')
        self.assertEqual(ps.push(self.a, self.g, ['test/one'], ['LOCATION.json'], 'seed')['status'], 'PUSHED_VERIFIED')
        self.b = base / 'b'
        ps.clone(self.g, self.b, url=str(self.remote))

    def tearDown(self):
        self.g.close()
        self.t.cleanup()

    def drop_config(self, repo):
        """What a workspace snapshot restore does: .git/config disappears."""
        (repo / '.git' / 'config').unlink()


class SnapshotRecovery(Base):
    def test_push_repairs_origin_and_identity_after_config_loss(self):
        self.drop_config(self.a)
        h.update(self.a, 'test/one', {'set': {'status': 'PAUSED'}}, self.ha['checkpoint_id'], 'pause')
        res = ps.push(self.a, self.g, ['test/one'], message='after snapshot')
        self.assertEqual(res['status'], 'PUSHED_VERIFIED')
        self.assertEqual(res['repairs'], ['origin'])
        self.assertEqual(res['identity'], 'last commit author')
        self.assertEqual(git('log', '-1', '--format=%an <%ae>', cwd=self.a), 'Owner Name <owner@example.com>')
        self.assertEqual(git('rev-parse', 'refs/heads/main', cwd=self.remote), res['commit'])

    def test_missing_origin_without_location_explains_fix(self):
        self.drop_config(self.a)
        (self.a / 'LOCATION.json').unlink()
        with self.assertRaises(ps.SyncError) as ctx:
            ps.status(self.a, self.g, fetch=True)
        self.assertIn('--remote-url', str(ctx.exception))
        self.assertEqual(ps.status(self.a, self.g, fetch=True, remote_url=str(self.remote))['repairs'], ['origin'])

    def test_credentialed_remote_url_refused(self):
        self.drop_config(self.a)
        with self.assertRaises(ps.SyncError):
            ps.ensure_origin(self.a, self.g, 'https://user:secret@github.com/o/r.git')


class Pull(Base):
    def test_fast_forward(self):
        h.checkpoint(self.b, 'test/two', state('Two'), 'NEW', 'two')
        ps.push(self.b, self.g, ['test/two'], message='two')
        res = ps.pull(self.a, self.g)
        self.assertEqual(res['status'], 'UPDATED')
        self.assertEqual(res['pulled_commits'], 1)
        self.assertEqual(len(h.validate(self.a)['projects']), 2)
        self.assertEqual(ps.pull(self.a, self.g)['status'], 'UP_TO_DATE')

    def test_rebases_local_commit_and_regenerates_index(self):
        h.checkpoint(self.b, 'test/two', state('Two'), 'NEW', 'two')
        ps.push(self.b, self.g, ['test/two'], message='two')
        h.update(self.a, 'test/one', {'set': {'status': 'PAUSED'}}, self.ha['checkpoint_id'], 'local work')
        ps.refresh_views(self.a, ['test/one'])
        git('add', '-A', cwd=self.a)
        git('commit', '-qm', 'local commit', cwd=self.a)
        res = ps.pull(self.a, self.g)
        self.assertEqual(res['status'], 'UPDATED')
        self.assertEqual(res['local_commits_rebased'], 1)
        self.assertTrue(h.validate(self.a)['index_current'])
        self.assertNotIn('<<<<<<<', (self.a / 'INDEX.md').read_text(encoding='utf-8'))

    def test_dirty_tree_blocks_pull_without_loss(self):
        h.checkpoint(self.b, 'test/two', state('Two'), 'NEW', 'two')
        ps.push(self.b, self.g, ['test/two'], message='two')
        (self.a / 'LOCATION.json').write_text('{"changed": true}')
        with self.assertRaises(ps.SyncError):
            ps.pull(self.a, self.g)
        self.assertEqual((self.a / 'LOCATION.json').read_text(), '{"changed": true}')

    def test_clone_is_rerunnable(self):
        self.drop_config(self.b)
        res = ps.clone(self.g, self.b, url=str(self.remote))
        self.assertIn(res['status'], {'UP_TO_DATE', 'UPDATED'})
        self.assertEqual(res['repairs'], ['origin'])


class Save(Base):
    def test_update_and_verified_push_in_one_step(self):
        res = ps.save(self.a, self.g, 'test/one', self.ha['checkpoint_id'], {'set': {'status': 'READY_FOR_REVIEW'}},
                      'ready for review')
        self.assertEqual(res['status'], 'PUSHED_VERIFIED')
        self.assertEqual(res['checkpoint']['revision'], 2)
        self.assertEqual(git('rev-parse', 'refs/heads/main', cwd=self.remote), res['sync']['commit'])

    def test_stale_expected_writes_and_pushes_nothing(self):
        before = git('rev-parse', 'refs/heads/main', cwd=self.remote)
        with self.assertRaises(h.HandoffError):
            ps.save(self.a, self.g, 'test/one', '20200101T000000Z-000000000000', {'set': {'status': 'PAUSED'}}, 'x')
        self.assertEqual(git('rev-parse', 'refs/heads/main', cwd=self.remote), before)

    def test_push_failure_reports_local_only_checkpoint(self):
        git('remote', 'set-url', 'origin', str(Path(self.t.name) / 'missing.git'), cwd=self.a)
        with self.assertRaises(ps.SyncError) as ctx:
            ps.save(self.a, self.g, 'test/one', self.ha['checkpoint_id'], {'set': {'status': 'PAUSED'}}, 'offline')
        self.assertIn('LOCAL_ONLY', str(ctx.exception))
        self.assertEqual(h.checked_head(h.project_dir(self.a, 'test/one'))[0]['revision'], 2)  # kept locally

    def test_cli_save_from_stdin(self):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps({'set': {'status': 'PAUSED'}}))
        try:
            with redirect_stdout(io.StringIO()) as out:
                code = ps.main(['--root', str(self.a), 'save', '--project', 'test/one', '--expected',
                                self.ha['checkpoint_id'], '--patch', '-', '--note', 'cli save'])
        finally:
            sys.stdin = old
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['status'], 'PUSHED_VERIFIED')


class Doctor(Base):
    def test_healthy(self):
        res = ps.doctor(self.a, self.g, token_present=True)
        self.assertTrue(res['ok'], res)

    def test_reports_problems_with_fixes(self):
        self.drop_config(self.a)
        h.checkpoint(self.b, 'test/two', state('Two'), 'NEW', 'two')
        ps.push(self.b, self.g, ['test/two'], message='two')
        (self.a / 'LOCATION.json').write_text(json.dumps({'progress_remote_url': str(self.remote), 'x': 1}))
        res = ps.doctor(self.a, self.g, token_present=False)
        by = {c['check']: c for c in res['checks']}
        self.assertFalse(res['ok'])
        self.assertIn('re-added', by['origin']['detail'])
        self.assertFalse(by['credentials']['ok'])
        self.assertFalse(by['remote']['ok'])
        self.assertIn('pull', by['remote']['fix'])
        self.assertFalse(by['working tree']['ok'])


class NewProject(unittest.TestCase):
    def test_new_command(self):
        with tempfile.TemporaryDirectory() as d:
            with redirect_stdout(io.StringIO()) as out:
                code = h.main(['--root', d, 'new', '--project', 'demo/first', '--title', 'First', '--goal', 'Goal',
                               '--next-action', 'Ask the user', '--related', 'demo/second'])
            self.assertEqual(code, 0)
            res = json.loads(out.getvalue())
            self.assertEqual((res['revision'], res['lint']), (1, []))
            s = h.checked_head(h.project_dir(Path(d), 'demo/first'))[1]['state']
            self.assertEqual((s['title'], s['related_projects']), ('First', ['demo/second']))
            self.assertTrue(s['handoff']['first_action'])
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                h.main(['--root', d, 'new', '--project', 'demo/x', '--title', 't', '--goal', 'g',
                        '--status', 'LOCAL_READY_PENDING_SYNC'])  # deprecated status not offered


if __name__ == '__main__':
    unittest.main()
