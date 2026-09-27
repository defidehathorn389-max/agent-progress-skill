"""v2.6: safe publishing of skill repositories, skill-clone checks in doctor, tags, outdated."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import handoff as h  # noqa: E402
import progress_sync as ps  # noqa: E402
import skills as sk  # noqa: E402


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode().strip()


def bare(base, name):
    r = base / name
    git('init', '--quiet', '--bare', str(r), cwd=base)
    git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=r)
    return r


def skill_dir(folder, desc='Formats markdown tables'):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'SKILL.md').write_text(f'---\nname: demo\ndescription: {desc}\n---\n# Demo\n', encoding='utf-8')
    return folder


class Publish(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.base = Path(self.t.name)
        self.remote = bare(self.base, 'rules.git')
        self.g = ps.Git()
        self.repo = self.base / 'rules'
        ps.clone(self.g, self.repo, url=str(self.remote))
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=self.repo)
        git('config', 'user.name', 'Owner', cwd=self.repo)
        git('config', 'user.email', 'owner@example.com', cwd=self.repo)
        (self.repo / 'LOCATION.json').write_text(json.dumps({'remote_url': str(self.remote), 'visibility': 'public'}))
        skill_dir(self.repo)
        self.prog = self.base / 'progress'
        self.prog.mkdir()
        (self.prog / 'PRIVACY_TERMS.json').write_text(json.dumps({'terms': ['秘密项目']}), encoding='utf-8')

    def tearDown(self):
        self.g.close()
        self.t.cleanup()

    def remote_head(self):
        return subprocess.run(['git', 'rev-parse', '--verify', '--quiet', 'refs/heads/main'], cwd=str(self.remote),
                              stdout=subprocess.PIPE, text=True).stdout.strip()

    def test_public_repo_blocked_by_privacy_scan_then_published(self):
        (self.repo / 'rules.md').write_text('lesson from 秘密项目: render in segments', encoding='utf-8')
        with self.assertRaises(sk.SkillError) as ctx:
            sk.publish(self.repo, 'promote lesson', progress_root=self.prog)
        self.assertIn('秘密项目', str(ctx.exception))
        self.assertEqual(self.remote_head(), '')  # nothing reached the remote
        (self.repo / 'rules.md').write_text('lesson: render long videos in segments', encoding='utf-8')
        res = sk.publish(self.repo, 'promote lesson', progress_root=self.prog)
        self.assertEqual((res['status'], res['privacy_scan']), ('PUSHED_VERIFIED', 'clean (1 terms)'))
        self.assertEqual(res['commit'], self.remote_head())
        (self.repo / '.git' / 'config').unlink()  # workspace snapshot restore
        (self.repo / 'rules.md').write_text('lesson: render in segments of 800 frames', encoding='utf-8')
        res = sk.publish(self.repo, 'refine lesson', progress_root=self.prog)
        self.assertEqual((res['status'], res['repairs']), ('PUSHED_VERIFIED', ['origin']))

    def test_private_repo_skips_privacy_scan_but_not_secret_checks(self):
        (self.repo / 'LOCATION.json').write_text(json.dumps({'remote_url': str(self.remote), 'visibility': 'private'}))
        (self.repo / 'notes.md').write_text('internal notes about 秘密项目', encoding='utf-8')
        res = sk.publish(self.repo, 'private notes', progress_root=self.prog)
        self.assertEqual(res['privacy_scan'], 'skipped (private repository)')
        (self.repo / 'leak.md').write_text('token ' + 'gh' + 'p_' + 'D' * 36, encoding='utf-8')
        with self.assertRaises(ps.SyncError):
            sk.publish(self.repo, 'leak', progress_root=self.prog)


class DoctorSkillClone(unittest.TestCase):
    def test_skill_clone_repaired_and_dirty_edits_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            g = ps.Git()
            try:
                skill_remote, prog_remote = bare(base, 'skill.git'), bare(base, 'progress.git')
                kd = base / 'skill'
                ps.clone(g, kd, url=str(skill_remote))
                git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=kd)
                (kd / 'LOCATION.json').write_text(json.dumps({'remote_url': str(skill_remote), 'visibility': 'public'}))
                skill_dir(kd)
                sk.publish(kd, 'init', progress_root=base)
                prog = base / 'progress'
                ps.clone(g, prog, url=str(prog_remote))
                git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=prog)
                (prog / 'LOCATION.json').write_text(json.dumps({'progress_remote_url': str(prog_remote),
                                                                'remote_entry_url': str(skill_remote)}))
                h.checkpoint(prog, 'test/one', h.new_state('One', 'g', next_actions=['x']), 'NEW', 'create')
                ps.push(prog, g, ['test/one'], ['LOCATION.json'], 'seed')
                (kd / '.git' / 'config').unlink()
                check = {c['check']: c for c in ps.doctor(prog, g, token_present=True, skill_dir=kd)['checks']}['skill version']
                self.assertTrue(check['ok'], check)
                self.assertIn('origin re-added', check['detail'])
                (kd / 'SKILL.md').write_text('---\nname: demo\ndescription: edited\n---\n', encoding='utf-8')
                check = {c['check']: c for c in ps.doctor(prog, g, token_present=True, skill_dir=kd)['checks']}['skill version']
                self.assertFalse(check['ok'])
                self.assertIn('uncommitted edit', check['detail'])
                self.assertIn('skills.py publish', check['fix'])
            finally:
                g.close()


class TagsAndOutdated(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.base = Path(self.t.name)
        self.root = self.base / 'agent-skills'
        self.root.mkdir()
        (self.root / 'SOURCES.json').write_text('{}')
        self.up = self.base / 'upstream'
        skill_dir(self.up / 'skills' / 'demo')
        (self.up / 'other.txt').write_text('unrelated')
        git('init', '-q', '-b', 'main', str(self.up), cwd=self.base)
        self.commit('init')

    def tearDown(self):
        self.t.cleanup()

    def commit(self, msg):
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '-A', cwd=self.up)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', msg, cwd=self.up)

    def test_tags_make_english_skills_findable_in_chinese(self):
        src = skill_dir(self.base / 'ext')
        sk.add(self.root, src, 'table-fmt', 'https://example.com/table-fmt', tags=['表格'])
        self.assertEqual([x['name'] for x in sk.search(self.root, '表格')], ['table-fmt'])
        sk.tag(self.root, 'table-fmt', ['排版'])
        self.assertEqual([x['name'] for x in sk.search(self.root, '排版')], ['table-fmt'])
        self.assertTrue(sk.verify(self.root)['pass'])  # tags live in SOURCE.json, file hashes unchanged
        with self.assertRaises(sk.SkillError):
            sk.tag(self.root, 'missing', ['x'])

    def test_outdated_detects_upstream_changes_in_the_skill_path_only(self):
        url = 'file://' + str(self.up)
        got = sk.fetch(url=url, path='skills/demo')
        sk.add(self.root, got['dir'], 'demo', 'https://example.com/demo', commit=got['commit'], path='skills/demo', clone_url=url)
        self.assertEqual(sk.outdated(self.root)[0]['status'], 'up_to_date')
        (self.up / 'other.txt').write_text('changed outside the skill')
        self.commit('outside')
        self.assertEqual(sk.outdated(self.root)[0]['status'], 'up_to_date')
        (self.up / 'skills' / 'demo' / 'SKILL.md').write_text('---\nname: demo\ndescription: v2\n---\n', encoding='utf-8')
        self.commit('skill v2')
        res = sk.outdated(self.root, 'demo')[0]
        self.assertEqual((res['status'], res['changed_files']), ('changed', ['SKILL.md']))
        self.assertIn('--replace', res['update'])


_WS_TMP = None


def setUpModule():  # doctor measures the persisted workspace: keep tests independent of the real one
    global _WS_TMP
    import os as _os
    import tempfile as _tempfile
    _WS_TMP = _tempfile.TemporaryDirectory()
    _os.environ['AGENT_WORKSPACE_HOME'] = _WS_TMP.name


def tearDownModule():
    import os as _os
    _os.environ.pop('AGENT_WORKSPACE_HOME', None)
    _WS_TMP.cleanup()


if __name__ == '__main__':
    unittest.main()
