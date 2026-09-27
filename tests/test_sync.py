"""progress_sync.py end-to-end against local bare repositories (no network)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import handoff as h  # noqa: E402
import progress_sync as ps  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / 'templates' / 'state.json'


def state(title='Project'):
    s = json.loads(TEMPLATE.read_text(encoding='utf-8'))
    s.update(title=title, goal='Sync safely')
    return s


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode().strip()


class SyncTests(unittest.TestCase):
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
        (self.a / '.gitignore').write_text('.write.lock\n*.tmp\n')
        self.ha = h.checkpoint(self.a, 'test/one', state('One'), 'NEW', 'create one')
        seeded = ps.push(self.a, self.g, ['test/one'], ['.gitignore'], 'seed')
        self.assertEqual(seeded['status'], 'PUSHED_VERIFIED')
        self.b = base / 'b'
        ps.clone(self.g, self.b, url=str(self.remote))

    def tearDown(self):
        self.g.close()
        self.t.cleanup()

    def remote_head(self):
        return git('rev-parse', 'refs/heads/main', cwd=self.remote)

    def test_status_states_and_verified_push(self):
        row = ps.status(self.a, self.g, fetch=True)['projects'][0]
        self.assertEqual(row['sync_state'], 'PUSHED_VERIFIED')
        h.update(self.a, 'test/one', {'set': {'status': 'READY_FOR_REVIEW'}}, self.ha['checkpoint_id'], 'review')
        self.assertEqual(ps.status(self.a, self.g, projects=['test/one'])['projects'][0]['sync_state'],
                         'LOCAL_ONLY_UNCOMMITTED')
        res = ps.push(self.a, self.g, ['test/one'], message='test/one: review')
        self.assertEqual(res['status'], 'PUSHED_VERIFIED')
        self.assertEqual(res['commit'], self.remote_head())
        self.assertEqual(git('log', '-1', '--format=%s', cwd=self.a), 'test/one: review')  # one commit, no receipt commit
        self.assertEqual(ps.status(self.a, self.g, fetch=True)['projects'][0]['sync_state'], 'PUSHED_VERIFIED')
        # B has not pulled yet: its local checkpoint is older than the remote one
        self.assertEqual(ps.status(self.b, self.g, fetch=True)['projects'][0]['sync_state'], 'REMOTE_AHEAD')

    def test_no_changes_means_no_commit(self):
        before = git('rev-parse', 'HEAD', cwd=self.a)
        res = ps.push(self.a, self.g, ['test/one'], message='noop')
        self.assertEqual(res['status'], 'NO_CHANGES')
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.a), before)

    def test_concurrent_other_project_rebases_and_regenerates_index(self):
        h.checkpoint(self.b, 'test/two', state('Two'), 'NEW', 'create two')
        self.assertEqual(ps.push(self.b, self.g, ['test/two'], message='two')['status'], 'PUSHED_VERIFIED')
        h.update(self.a, 'test/one', {'set': {'status': 'PAUSED'}}, self.ha['checkpoint_id'], 'pause one')
        res = ps.push(self.a, self.g, ['test/one'], message='one: pause')
        self.assertEqual(res['status'], 'PUSHED_VERIFIED')
        self.assertTrue(res['rebased_onto_remote'])
        index = (self.a / 'INDEX.md').read_text(encoding='utf-8')
        self.assertIn('`test/one`', index)
        self.assertIn('`test/two`', index)
        self.assertNotIn('<<<<<<<', index)
        check = h.validate(self.a)
        self.assertTrue(check['index_current'])
        self.assertEqual(len(check['projects']), 2)

    def test_same_project_conflict_is_refused_without_loss(self):
        hb = h.checked_head(h.project_dir(self.b, 'test/one'))[0]
        h.update(self.b, 'test/one', {'set': {'goal': 'B'}}, hb['checkpoint_id'], 'b')
        ps.push(self.b, self.g, ['test/one'], message='b')
        remote_before = self.remote_head()
        h.update(self.a, 'test/one', {'set': {'goal': 'A'}}, self.ha['checkpoint_id'], 'a')
        with self.assertRaises(ps.SyncError) as ctx:
            ps.push(self.a, self.g, ['test/one'], message='a')
        self.assertIn('merge-parent', str(ctx.exception))
        self.assertEqual(self.remote_head(), remote_before)             # nothing forced
        self.assertFalse((self.a / '.git' / 'rebase-merge').exists())   # rebase aborted cleanly
        self.assertEqual(git('log', '-1', '--format=%s', cwd=self.a), 'a')  # local work kept
        self.assertEqual(ps.status(self.a, self.g, fetch=True)['projects'][0]['sync_state'], 'DIVERGED')

    def test_secret_and_credential_files_refused(self):
        (self.a / 'notes.md').write_text('leak ' + 'gh' + 'p_' + 'A' * 36)
        with self.assertRaises(ps.SyncError):
            ps.push(self.a, self.g, ['test/one'], ['notes.md'], 'bad')
        self.assertEqual(git('diff', '--cached', '--name-only', cwd=self.a), '')
        (self.a / 'notes.md').unlink()
        (self.a / 'vault.enc.json').write_text('{}')
        with self.assertRaises(ps.SyncError):
            ps.push(self.a, self.g, ['test/one'], ['vault.enc.json'], 'bad')

    def test_unrelated_dirty_tracked_file_blocks_before_commit(self):
        (self.a / '.gitignore').write_text('changed\n')
        h.update(self.a, 'test/one', {'set': {'status': 'PAUSED'}}, self.ha['checkpoint_id'], 'p')
        before = git('rev-parse', 'HEAD', cwd=self.a)
        with self.assertRaises(ps.SyncError):
            ps.push(self.a, self.g, ['test/one'], message='p')
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.a), before)

    def test_dry_run_changes_nothing(self):
        h.update(self.a, 'test/one', {'set': {'status': 'PAUSED'}}, self.ha['checkpoint_id'], 'p')
        before = git('rev-parse', 'HEAD', cwd=self.a)
        res = ps.push(self.a, self.g, ['test/one'], message='p', dry_run=True)
        self.assertEqual(res['status'], 'DRY_RUN')
        self.assertTrue(res['would_stage'])
        self.assertEqual(res['problems'], [])
        self.assertEqual(git('rev-parse', 'HEAD', cwd=self.a), before)
        self.assertEqual(git('diff', '--cached', '--name-only', cwd=self.a), '')

    def test_token_only_in_memory(self):
        secret = 'synthetic-token-value-123'
        g = ps.Git(token=secret)
        helper = Path(g.env['GIT_ASKPASS'])
        self.assertNotIn(secret, helper.read_text())
        ask = lambda prompt: subprocess.run([str(helper), prompt], env=g.env, stdout=subprocess.PIPE).stdout.decode().strip()
        self.assertEqual(ask("Password for 'https://x-access-token@github.com': "), secret)
        self.assertEqual(ask("Username for 'https://github.com': "), 'x-access-token')
        self.assertNotIn(secret, g.redact('fatal: ' + secret + ' https://user:pw@github.com/x'))
        self.assertNotIn('pw@', g.redact('https://user:pw@github.com/x'))
        g.close()
        self.assertFalse(helper.exists())
        self.assertNotIn(secret, (self.a / '.git' / 'config').read_text())
        with self.assertRaises(ps.SyncError):
            ps.read_token(self.a / 'token.txt', self.a)  # token files inside the repo are refused


if __name__ == '__main__':
    unittest.main()
