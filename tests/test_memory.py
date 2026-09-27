"""agent-memory tool: items, dedupe, self-evolution log, brief ordering/scope, expiry, safety, sync, doctor."""
import datetime
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
import memory as m  # noqa: E402
import progress_sync as ps  # noqa: E402
import skills as sk  # noqa: E402


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode().strip()


class Items(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name) / 'agent-memory'

    def tearDown(self):
        self.t.cleanup()

    def test_upsert_dedupe_and_log(self):
        a = m.upsert(self.r, 'preference', {'text': '回复用中文', 'source': 'inferred', 'scope': 'global',
                                            'evidence': '用户一直用中文提问'}, why='观察到')
        b = m.upsert(self.r, 'preference', {'text': '回复用中文', 'source': 'explicit', 'scope': 'global',
                                            'evidence': '用户明确说过'}, why='用户确认')
        self.assertEqual((a['action'], b['action'], a['id']), ('add', 'update', b['id']))
        pref = m.active(self.r, 'preference')[0]
        self.assertEqual((pref['source'], len(m.active(self.r, 'preference'))), ('explicit', 1))
        l1 = m.upsert(self.r, 'lesson', {'title': '快照丢失远端配置', 'rule': '先运行 doctor', 'trigger': 'failure',
                                         'scope': 'global', 'tags': ['git']})
        l2 = m.upsert(self.r, 'lesson', {'title': '快照丢失远端配置', 'rule': '先运行 doctor（会自动修复）',
                                         'trigger': 'failure', 'scope': 'global', 'tags': ['sandbox']})
        self.assertEqual((l1['id'], l2['count']), (l2['id'], 2))
        lesson = m.active(self.r, 'lesson')[0]
        self.assertEqual((lesson['rule'], lesson['tags']), ('先运行 doctor（会自动修复）', ['git', 'sandbox']))
        self.assertEqual(len(list((self.r / 'changes').glob('*.json'))), 4)
        changelog = (self.r / 'CHANGELOG.md').read_text(encoding='utf-8')
        self.assertIn('用户确认', changelog)
        self.assertIn('快照丢失远端配置', (self.r / 'MEMORY.md').read_text(encoding='utf-8'))
        self.assertTrue(m.validate(self.r)['views_current'])

    def test_retire_hides_but_keeps(self):
        p = m.upsert(self.r, 'preference', {'text': '回复尽量长', 'source': 'inferred', 'scope': 'global', 'evidence': 'x'})
        m.retire(self.r, p['id'], '用户否定')
        self.assertEqual(m.active(self.r, 'preference'), [])
        self.assertNotIn('回复尽量长', m.brief(self.r))
        self.assertIn('已停用 1', (self.r / 'MEMORY.md').read_text(encoding='utf-8'))
        with self.assertRaises(m.MemError):
            m.retire(self.r, p['id'], 'again')

    def test_rejections(self):
        bad = [('preference', {'text': 'x', 'source': 'explicit', 'scope': 'everywhere', 'evidence': 'e'}),
               ('preference', {'text': 'x', 'source': 'guess', 'scope': 'global', 'evidence': 'e'}),
               ('lesson', {'title': 't', 'rule': 'r', 'trigger': 'oops', 'scope': 'global'}),
               ('fact', {'text': 'token is ' + 'gh' + 'p_' + 'A' * 36, 'evidence': 'e'}),
               ('fact', {'text': 'y' * (m.MAX_FIELD + 1), 'evidence': 'e'})]
        for kind, fields in bad:
            with self.subTest(fields=str(fields)[:40]), self.assertRaises(m.MemError):
                m.upsert(self.r, kind, fields)
        self.assertEqual(list((self.r / 'items' / 'facts').glob('*.json')), [])

    def test_validate_never_modifies(self):
        m.upsert(self.r, 'fact', {'text': '主要做中文科普视频', 'evidence': '项目列表'})
        (self.r / 'MEMORY.md').write_text('hand edited', encoding='utf-8')
        self.assertFalse(m.validate(self.r)['views_current'])
        self.assertEqual((self.r / 'MEMORY.md').read_text(encoding='utf-8'), 'hand edited')


class BriefAndExpiry(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name)
        self.now = datetime.datetime(2026, 9, 27, 12, 0, tzinfo=datetime.timezone.utc)
        self.clock = mock.patch.object(m, 'now_dt', lambda: self.now)
        self.clock.start()
        m.upsert(self.r, 'preference', {'text': '猜你喜欢简短', 'source': 'inferred', 'scope': 'global', 'evidence': '总选短的'})
        m.upsert(self.r, 'preference', {'text': '每次回复都给选项', 'source': 'explicit', 'scope': 'global', 'evidence': '原话'})
        m.upsert(self.r, 'lesson', {'title': '全局坑', 'rule': 'g', 'trigger': 'failure', 'scope': 'global'})
        m.upsert(self.r, 'lesson', {'title': '视频坑', 'rule': 'v', 'trigger': 'rework', 'scope': 'domain:video'})
        m.upsert(self.r, 'lesson', {'title': '项目坑', 'rule': 'p', 'trigger': 'user_correction', 'scope': 'project:video/a'})
        m.upsert(self.r, 'lesson', {'title': '别的项目', 'rule': 'o', 'trigger': 'other', 'scope': 'project:video/b'})
        m.add_short_term(self.r, 'temporary', {'text': '这周先别推送'}, 1)
        m.add_short_term(self.r, 'session', {'summary': '优化了进度 skill', 'open_items': ['建记忆库']}, 30)
        m.set_focus(self.r, ['workflow/memory|建记忆库|1', 'video/a|终审|2'])

    def tearDown(self):
        self.clock.stop()
        self.t.cleanup()

    def test_brief_order_and_scope(self):
        text = m.brief(self.r, project='video/a')
        self.assertLess(text.index('每次回复都给选项'), text.index('猜你喜欢简短'))  # explicit first
        lessons = text.split('## 需要避开的坑')[1].split('##')[0]
        self.assertLess(lessons.index('项目坑'), lessons.index('视频坑'))
        self.assertLess(lessons.index('视频坑'), lessons.index('全局坑'))
        self.assertNotIn('别的项目', text)
        for needle in ('这周先别推送', '`workflow/memory` — 建记忆库', '优化了进度 skill', '未完成：建记忆库', '7 天内过期'):
            self.assertIn(needle, text)
        self.assertNotIn('视频坑', m.brief(self.r))  # no project: global scope only

    def test_expiry(self):
        self.now = self.now + datetime.timedelta(days=2)
        self.assertIn('这周先别推送', (self.r / 'MEMORY.md').read_text(encoding='utf-8'))
        res = m.expire(self.r)
        self.assertEqual(len(res['expired']), 1)
        self.assertNotIn('这周先别推送', m.brief(self.r))
        self.assertIn('expire', (self.r / 'CHANGELOG.md').read_text(encoding='utf-8'))
        self.assertEqual(m.validate(self.r)['expired_pending'], [])


class SyncAndDoctor(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        base = Path(self.t.name)
        self.remote = base / 'memory.git'
        git('init', '--quiet', '--bare', str(self.remote), cwd=base)
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=self.remote)
        self.g = ps.Git()
        self.a, self.b = base / 'a', base / 'b'
        ps.clone(self.g, self.a, url=str(self.remote))
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=self.a)
        (self.a / 'LOCATION.json').write_text(json.dumps({'remote_url': str(self.remote)}))
        m.upsert(self.a, 'fact', {'text': 'seed', 'evidence': 'test'})
        self.assertEqual(m.sync(self.a, 'seed')['status'], 'PUSHED_VERIFIED')
        ps.clone(self.g, self.b, url=str(self.remote))

    def tearDown(self):
        self.g.close()
        self.t.cleanup()

    def test_parallel_sessions_merge_by_regenerating_views(self):
        m.upsert(self.b, 'lesson', {'title': 'B 的坑', 'rule': 'rb', 'trigger': 'failure', 'scope': 'global'})
        self.assertEqual(m.sync(self.b, 'b')['status'], 'PUSHED_VERIFIED')
        m.upsert(self.a, 'lesson', {'title': 'A 的坑', 'rule': 'ra', 'trigger': 'failure', 'scope': 'global'})
        res = m.sync(self.a, 'a')
        self.assertEqual(res['status'], 'PUSHED_VERIFIED')
        self.assertTrue(set(res['derived_conflicts_regenerated']) <= set(m.DERIVED))
        text = (self.a / 'MEMORY.md').read_text(encoding='utf-8')
        self.assertIn('A 的坑', text)
        self.assertIn('B 的坑', text)
        self.assertNotIn('<<<<<<<', text)
        self.assertTrue(m.validate(self.a)['views_current'])
        self.assertEqual(m.sync(self.a, 'noop')['status'], 'NO_CHANGES')

    def test_doctor_checks_companion_repositories(self):
        base = Path(self.t.name)
        prog_remote, skills_remote = base / 'progress.git', base / 'skills.git'
        for r in (prog_remote, skills_remote):
            git('init', '--quiet', '--bare', str(r), cwd=base)
            git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=r)
        prog, sroot = base / 'progress', base / 'skills'
        ps.clone(self.g, prog, url=str(prog_remote))
        git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=prog)
        (prog / 'LOCATION.json').write_text(json.dumps({'progress_remote_url': str(prog_remote),
                                                        'memory_remote_url': str(self.remote),
                                                        'skills_remote_url': str(skills_remote)}))
        h.checkpoint(prog, 'test/one', h.new_state('One', 'g', next_actions=['x']), 'NEW', 'create')
        ps.push(prog, self.g, ['test/one'], ['LOCATION.json'], 'seed')
        env = {'AGENT_MEMORY_ROOT': str(self.a), 'AGENT_SKILLS_ROOT': str(sroot)}
        with mock.patch.dict(os.environ, env):
            by = {c['check']: c for c in ps.doctor(prog, self.g, token_present=True)['checks']}
            self.assertTrue(by['memory repo']['ok'], by['memory repo'])
            self.assertFalse(by['skills repo']['ok'])
            self.assertIn('clone', by['skills repo']['fix'])
            ps.clone(self.g, sroot, url=str(skills_remote))
            git('symbolic-ref', 'HEAD', 'refs/heads/main', cwd=sroot)
            (sroot / 'LOCATION.json').write_text(json.dumps({'remote_url': str(skills_remote)}))
            sk.index(sroot)
            sk.sync(sroot, 'init skills')
            by = {c['check']: c for c in ps.doctor(prog, self.g, token_present=True)['checks']}
            self.assertTrue(by['skills repo']['ok'], by['skills repo'])
            m.upsert(self.a, 'fact', {'text': 'unsynced', 'evidence': 'x'})
            by = {c['check']: c for c in ps.doctor(prog, self.g, token_present=True)['checks']}
            self.assertFalse(by['memory repo']['ok'])  # uncommitted memory must be synced


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
