"""v2.3.1: ALIASES.json navigation (own name outranks mentions elsewhere), exporter hardening."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import export_handoff as ex  # noqa: E402
import handoff as h  # noqa: E402
import progress_sync as ps  # noqa: E402


class Aliases(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name)
        h.checkpoint(self.r, 'video/series', h.new_state('Episode 25 final cut', 'finish the puzzle series',
                                                         next_actions=['x']), 'NEW', 'create')
        other = h.new_state('Workflow: tidied the series project state', 'series series series', next_actions=['x'])
        h.checkpoint(self.r, 'workflow/meta', other, 'NEW', 'mentions the series a lot')

    def tearDown(self):
        self.t.cleanup()

    def write_aliases(self, projects):
        (self.r / 'ALIASES.json').write_text(json.dumps({'schema': 'agent-progress-aliases/1', 'projects': projects}),
                                             encoding='utf-8')

    def test_own_alias_outranks_mentions_elsewhere(self):
        self.assertEqual(h.find(self.r, 'series')[0]['project'], 'workflow/meta')  # without aliases: wrong project
        self.write_aliases({'video/series': ['series', '谜题动画']})
        top = h.find(self.r, 'series')[0]
        self.assertEqual((top['project'], 'alias' in top['matched']), ('video/series', True))
        self.assertEqual(h.find(self.r, '上次那个谜题动画的第25期')[0]['project'], 'video/series')  # query contains alias

    def test_context_aliases_and_bad_file(self):
        (self.r / 'ALIASES.json').write_text('{not json', encoding='utf-8')
        self.assertEqual(h.load_aliases(self.r), {})  # broken file never breaks find
        cid = h.checked_head(h.project_dir(self.r, 'video/series'))[0]['checkpoint_id']
        h.update(self.r, 'video/series', {'set': {'context.aliases': ['puzzle-show']}}, cid, 'alias')
        self.assertEqual(h.find(self.r, 'puzzle-show')[0]['project'], 'video/series')

    def test_doctor_flags_unknown_alias_projects_as_advisory(self):
        self.write_aliases({'video/series': ['series'], 'video/ghost': ['ghost']})
        g = ps.Git()
        try:
            import subprocess
            subprocess.run(['git', 'init', '-q', '-b', 'main', str(self.r)], check=True)
            res = ps.doctor(self.r, g, token_present=True, remote_url=str(self.r))
        finally:
            g.close()
        check = {c['check']: c for c in res['checks']}['aliases']
        self.assertFalse(check['ok'])
        self.assertTrue(check['advisory'])
        self.assertIn('video/ghost', check['detail'])


class Exporter(unittest.TestCase):
    def test_secrets_dir_skipped_and_oversized_reported(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / 'kit' / '.secrets').mkdir(parents=True)
            (ws / 'kit' / '.secrets' / 'token.txt').write_text('not-a-pattern-but-private')
            (ws / 'kit' / 'notes.md').write_text('hello')
            (ws / 'kit' / 'big.json').write_text('x' * (2 * 1024 * 1024 + 10))
            res = ex.export(ws, ['kit'], ws / 'out' / 'pkg.zip')
            self.assertEqual(res['oversized_text_excluded'], ['kit/big.json'])
            with zipfile.ZipFile(ws / 'out' / 'pkg.zip') as z:
                names = z.namelist()
                manifest = json.loads(z.read('PACKAGE_MANIFEST.json'))
            self.assertIn('kit/notes.md', names)
            self.assertNotIn('kit/.secrets/token.txt', names)
            self.assertIn('kit/.secrets/token.txt', manifest['excluded_paths'])
            self.assertEqual(manifest['oversized_text_excluded'], ['kit/big.json'])


if __name__ == '__main__':
    unittest.main()
