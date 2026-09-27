"""v2.2: same-project divergence recovery (reconcile + merge-parent + push), compact, log, INDEX marker."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import handoff as h  # noqa: E402
import progress_sync as ps  # noqa: E402


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode().strip()


class Divergence(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        base = Path(self.t.name)
        self.remote = base / 'remote.git'
        git('init', '--quiet', '--bare', str(self.remote), cwd=base)
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=self.remote)
        self.g = ps.Git()
        self.a, self.b = base / 'a', base / 'b'
        ps.clone(self.g, self.a, url=str(self.remote))
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=self.a)
        self.base = h.checkpoint(self.a, 'test/one', h.new_state('One', 'g', next_actions=['x']), 'NEW', 'create')
        ps.push(self.a, self.g, ['test/one'], message='seed')
        ps.clone(self.g, self.b, url=str(self.remote))
        # both sessions advance the same project from the same base
        self.tb = h.update(self.b, 'test/one', {'append': {'constraints': ['from B']}}, self.base['checkpoint_id'], 'B work')
        ps.push(self.b, self.g, ['test/one'], message='B')
        self.oa = h.update(self.a, 'test/one', {'append': {'avoid': ['from A']}}, self.base['checkpoint_id'], 'A work')

    def tearDown(self):
        self.g.close()
        self.t.cleanup()

    def test_full_recovery_path(self):
        with self.assertRaises(ps.SyncError) as ctx:
            ps.push(self.a, self.g, ['test/one'], message='A')
        self.assertIn('reconcile --project test/one', str(ctx.exception))
        rec = ps.reconcile(self.a, self.g, 'test/one')
        self.assertEqual(rec['status'], 'DIVERGED')
        self.assertEqual(rec['common_ancestor'], self.base['checkpoint_id'])
        self.assertEqual(rec['differences']['constraints']['only_theirs'], ['"from B"'])
        self.assertEqual(rec['differences']['avoid']['only_ours'], ['"from A"'])
        merged = h.checked_head(h.project_dir(self.a, 'test/one'))[1]['state']
        merged['constraints'].append('from B')
        m = h.checkpoint(self.a, 'test/one', merged, self.oa['checkpoint_id'], 'merge A+B',
                         merge_parent=self.tb['checkpoint_id'])
        res = ps.push(self.a, self.g, ['test/one'], message='merge')
        self.assertEqual(res['status'], 'PUSHED_VERIFIED')
        self.assertIn('merge', res['derived_conflicts_regenerated'])
        self.assertEqual(ps.pull(self.b, self.g)['status'], 'UPDATED')
        head, cp = h.checked_head(h.project_dir(self.b, 'test/one'))
        self.assertEqual(head['checkpoint_id'], m['checkpoint_id'])
        self.assertEqual(len(cp['parents']), 2)
        self.assertEqual((cp['state']['constraints'], cp['state']['avoid']), (['from B'], ['from A']))
        self.assertEqual(h.validate(self.b)['projects'][0]['checkpoints_checked'], 4)
        self.assertEqual(ps.status(self.b, self.g, fetch=True)['projects'][0]['sync_state'], 'PUSHED_VERIFIED')

    def test_reconcile_states(self):
        self.assertEqual(ps.reconcile(self.b, self.g, 'test/one')['status'], 'IN_SYNC')
        rec = ps.reconcile(self.a, self.g, 'test/one')
        self.assertEqual(rec['status'], 'DIVERGED')
        self.assertEqual(rec['copied_checkpoints'], [self.tb['checkpoint_id']])
        # HEAD.json untouched by reconcile
        self.assertEqual(h.checked_head(h.project_dir(self.a, 'test/one'))[0]['checkpoint_id'], self.oa['checkpoint_id'])


class Compact(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name) / 'agent-progress'
        s = h.new_state('Big', 'g', next_actions=['x'])
        s['handoff'].update({f'detail_{i}': 'v' * 50 for i in range(20)})
        s['evidence'] = [{'id': f'ev{i}', 'kind': 'k', 'source': 's', 'scope': 'x'} for i in range(40)]
        s['completed'] = [{'item': f'done {i}', 'evidence': [f'ev{i}']} for i in range(30)]
        s['decisions'] = [{'id': 'old', 'decision': 'a'}, {'id': 'new', 'decision': 'b', 'supersedes': 'old'}]
        s['artifacts'] = [{'id': f'a{i}', 'role': 'r', 'availability': 'REMOTE_ONLY', 'sha256': '0' * 64,
                           'remote': {'repository': 'o/r', 'path': f'p{i}'}} for i in range(5)]
        s['sync'] = {'memory': 'PENDING_SYNC'}
        self.cp = h.checkpoint(self.r, 'test/big', s, 'NEW', 'create')

    def tearDown(self):
        self.t.cleanup()

    def test_dry_run_then_apply(self):
        dry = h.compact(self.r, 'test/big', self.cp['checkpoint_id'], keep_completed=10, dry_run=True,
                        externalize_artifacts=True)
        self.assertTrue(dry['dry_run'])
        self.assertLess(dry['after_bytes'], dry['before_bytes'])
        self.assertFalse((self.r / dry['artifacts_manifest']).exists())
        self.assertEqual(h.checked_head(h.project_dir(self.r, 'test/big'))[0]['revision'], 1)
        res = h.compact(self.r, 'test/big', self.cp['checkpoint_id'], keep_completed=10, externalize_artifacts=True)
        self.assertEqual(res['revision'], 2)
        s = h.checked_head(h.project_dir(self.r, 'test/big'))[1]['state']
        self.assertEqual(sorted(s['handoff']), ['first_action', 'running_operations'])
        self.assertEqual(len(s['context']['handoff_details']), 20)
        self.assertEqual(s['context']['compacted_from'], self.cp['checkpoint_id'])
        self.assertEqual(len(s['completed']), 10)
        self.assertEqual([d['id'] for d in s['decisions']], ['new'])
        kept = {e['id'] for e in s['evidence']}
        self.assertTrue({c['evidence'][0] for c in s['completed']} <= kept)  # references stay resolvable
        self.assertNotIn('memory', s['sync'])
        self.assertEqual([a['id'] for a in s['artifacts']], ['artifacts-manifest'])
        manifest = self.r / s['artifacts'][0]['remote']['path']
        self.assertEqual(len(json.loads(manifest.read_bytes())['artifacts']), 5)
        check = h.validate(self.r, 'test/big', workspace=self.r.parent)
        self.assertEqual(check['projects'][0]['local_artifacts_verified'], ['artifacts-manifest'])
        with self.assertRaises(h.HandoffError):
            h.compact(self.r, 'test/big', res['checkpoint_id'], keep_completed=10)  # nothing left to compact


class LogResumeIndex(unittest.TestCase):
    def test_log_resume_and_waiting_marker(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            c = h.checkpoint(r, 'test/log', h.new_state('L', 'g', next_actions=['x']), 'NEW', 'first note')
            for i in range(3):
                c = h.update(r, 'test/log', {'set': {'goal': f'g{i}'}}, c['checkpoint_id'], f'note {i}')
            c = h.update(r, 'test/log', {'set': {'handoff.waiting_for': 'user review'}}, c['checkpoint_id'], 'wait')
            log = h.history(h.walk_chain(h.project_dir(r, 'test/log'), h.checked_head(h.project_dir(r, 'test/log'))[1])[0], 3)
            self.assertEqual([x['revision'] for x in log], [5, 4, 3])
            brief = h.resume_text(r, 'test/log')
            self.assertIn('## 之前的记录（最近 4 次', brief)
            self.assertIn('note 2', brief)
            self.assertIn('⏳ user review', (r / 'INDEX.md').read_text(encoding='utf-8'))
            h.update(r, 'test/log', {'set': {'status': 'COMPLETED', 'next_actions': []}}, c['checkpoint_id'], 'done')
            self.assertNotIn('⏳', (r / 'INDEX.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
