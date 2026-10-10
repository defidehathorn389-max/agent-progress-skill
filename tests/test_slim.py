"""Slim clone: recent checkpoints only (default 3 days), newest per project kept, evidence excluded."""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import progress_sync as ps  # noqa: E402


def sh(*args, cwd):
    return subprocess.run(list(args), cwd=str(cwd), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()


def ts_days_ago(days, hash_='a' * 12):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return t.strftime('%Y%m%dT%H%M%SZ') + '-' + hash_


class SlimPatternTest(unittest.TestCase):
    def test_patterns_keep_recent_and_newest_only(self):
        now = datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc)
        old = 'projects/video-production/a/checkpoints/20260901T000000Z-aaaaaaaaaaaa.json'
        mid = 'projects/video-production/a/checkpoints/20260920T000000Z-bbbbbbbbbbbb.json'
        new = 'projects/video-production/a/checkpoints/20261009T000000Z-cccccccccccc.json'
        only_old = 'projects/workflow/b/checkpoints/20260801T000000Z-dddddddddddd.json'
        ev = 'projects/video-production/a/evidence/x.bin'
        pats, excluded = ps.slim_patterns([old, mid, new, only_old, ev, 'HEAD.json'], days=3, now=now)
        self.assertIn('!/' + old, pats)
        self.assertIn('!/' + mid, pats)
        self.assertNotIn('!/' + new, pats)
        self.assertNotIn('!/' + only_old, pats)  # newest of its project is always kept
        self.assertEqual(excluded, 2)
        self.assertIn('!/projects/**/evidence/', pats)

    def test_days_zero_keeps_all_checkpoints(self):
        pats, excluded = ps.slim_patterns(['projects/x/y/checkpoints/20200101T000000Z-aaaaaaaaaaaa.json'], days=0)
        self.assertEqual(excluded, 0)

    def test_escape_special_characters(self):
        self.assertEqual(ps.gitignore_escape('a b#[x]*.json'), 'a\\ b\\#\\[x\\]\\*.json')


class SlimCloneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='slim-test-'))
        src = self.tmp / 'src'
        src.mkdir()
        sh('git', 'init', '-q', '-b', 'main', cwd=src)
        sh('git', 'config', 'user.email', 't@example.com', cwd=src)
        sh('git', 'config', 'user.name', 't', cwd=src)
        cp = src / 'projects' / 'video-production' / 'a' / 'checkpoints'
        cp.mkdir(parents=True)
        (cp / '20200101T000000Z-111111111111.json').write_text('{"old":true}', encoding='utf-8')
        (cp / (ts_days_ago(1, '222222222222') + '.json')).write_text('{"recent":true}', encoding='utf-8')
        ev = src / 'projects' / 'video-production' / 'a' / 'evidence'
        ev.mkdir()
        (ev / 'big.bin').write_bytes(b'x' * 1000)
        (src / 'evidence').mkdir()
        (src / 'evidence' / 'top.bin').write_bytes(b'y' * 1000)
        (src / 'HEAD.json').write_text('{}', encoding='utf-8')
        sh('git', 'add', '-A', cwd=src)
        sh('git', 'commit', '-q', '-m', 'init', cwd=src)
        self.url = str(self.tmp / 'src')
        self.dest = self.tmp / 'clone'

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_slim_clone_and_reslim_on_pull(self):
        with ps.Git() as git:
            res = ps.clone(git, self.dest, url=self.url, slim=True, recent_days=3)
            self.assertEqual(res['recent_days'], 3)
            self.assertFalse((self.dest / 'evidence').exists())
            self.assertFalse((self.dest / 'projects/video-production/a/evidence').exists())
            self.assertFalse((self.dest / 'projects/video-production/a/checkpoints/20200101T000000Z-111111111111.json').exists())
            # the newest checkpoint is kept even when it is old
            self.assertTrue(any((self.dest / 'projects/video-production/a/checkpoints').iterdir()))
            self.assertTrue((self.dest / 'HEAD.json').exists())
            # history still complete: the old blob is reachable in git
            self.assertEqual(sh('git', 'rev-list', '--count', 'HEAD', cwd=self.dest), '1')
            # a second clone call on the existing slim clone reuses it and keeps the window
            again = ps.clone(git, self.dest, url=self.url, slim=True, recent_days=3)
            self.assertEqual(again['note'], 'existing clone reused')
            self.assertFalse((self.dest / 'evidence').exists())
            self.assertIsNotNone(ps.maybe_reslim(self.dest, git))

    def test_full_clone_unchanged_without_slim(self):
        with ps.Git() as git:
            ps.clone(git, self.dest, url=self.url, slim=False)
            self.assertTrue((self.dest / 'evidence' / 'top.bin').exists())


if __name__ == '__main__':
    unittest.main()
