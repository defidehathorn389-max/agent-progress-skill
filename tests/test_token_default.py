"""Default token file: used when no --token-file is given; never hard-coded to /home/user."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import handoff  # noqa: E402
import progress_sync  # noqa: E402

CLEAN = {k: v for k, v in os.environ.items() if k not in ('GH_TOKEN', 'GITHUB_TOKEN', 'GITHUB_TOKEN_FILE', 'SESAME_ENV_CONF')}


class TokenDefault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, **kw):
        e = dict(CLEAN, HOME=str(self.home))
        e.update(kw)
        return mock.patch.dict(os.environ, e, clear=True)

    def test_none_when_nothing(self):
        with self.env():
            self.assertIsNone(handoff.default_token_file())
            self.assertIsNone(handoff.default_token())

    def test_standard_secrets_file(self):
        f = self.home / '.config' / 'sesame-env' / 'secrets' / 'github_token'
        f.parent.mkdir(parents=True)
        f.write_text('tok-standard\n')
        with self.env():
            self.assertEqual(handoff.default_token_file(), f)
            self.assertEqual(handoff.default_token(), 'tok-standard')
            self.assertEqual(progress_sync.read_token(None, self.home / 'repo'), 'tok-standard')

    def test_env_file_and_conf_dir(self):
        a = self.home / 'a'; a.write_text('tok-a')
        c = self.home / 'conf' / 'secrets'; c.mkdir(parents=True); (c / 'github_token').write_text('tok-c')
        with self.env(GITHUB_TOKEN_FILE=str(a), SESAME_ENV_CONF=str(self.home / 'conf')):
            self.assertEqual(handoff.default_token(), 'tok-a')
        with self.env(SESAME_ENV_CONF=str(self.home / 'conf')):
            self.assertEqual(handoff.default_token(), 'tok-c')

    def test_env_token_wins_and_flag_wins(self):
        f = self.home / '.config' / 'sesame-env' / 'secrets' / 'github_token'
        f.parent.mkdir(parents=True); f.write_text('tok-file')
        flag = self.home / 'flag'; flag.write_text('tok-flag')
        with self.env(GH_TOKEN='tok-env'):
            self.assertEqual(progress_sync.read_token(None, self.home / 'repo'), 'tok-env')
            self.assertEqual(progress_sync.read_token(flag, self.home / 'repo'), 'tok-flag')

    def test_resume_hint_has_no_hardcoded_path(self):
        src = (Path(handoff.__file__)).read_text(encoding='utf-8')
        self.assertNotIn('/home/user/.secrets/github_token', src)


if __name__ == '__main__':
    unittest.main()
