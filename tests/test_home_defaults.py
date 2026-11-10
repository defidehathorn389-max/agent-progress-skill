"""Default roots follow the current user's home directory (no /home/user), env vars still override."""
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


class HomeDefaults(unittest.TestCase):
    def reload(self, name, **env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith('AGENT_')}
        clean.update(env)
        with mock.patch.dict(os.environ, clean, clear=True):
            return importlib.reload(importlib.import_module(name))

    def tearDown(self):
        for n in ('handoff', 'memory', 'skills'):
            importlib.reload(importlib.import_module(n))

    def test_defaults_under_home(self):
        home = Path.home()
        self.assertEqual(self.reload('handoff').DEFAULT_ROOT, home / 'agent-progress')
        self.assertEqual(self.reload('memory').DEFAULT_ROOT, home / 'agent-memory')
        self.assertEqual(self.reload('skills').DEFAULT_ROOT, home / 'agent-skills')

    def test_env_overrides(self):
        self.assertEqual(self.reload('memory', AGENT_MEMORY_ROOT='/x/mem').DEFAULT_ROOT, Path('/x/mem'))
        self.assertEqual(self.reload('handoff', AGENT_PROGRESS_ROOT='/x/p').DEFAULT_ROOT, Path('/x/p'))

    def test_no_hardcoded_home_user_in_scripts(self):
        for f in SCRIPTS.glob('*.py'):
            self.assertNotIn("'/home/user", f.read_text(encoding='utf-8'), f.name)


if __name__ == '__main__':
    unittest.main()
