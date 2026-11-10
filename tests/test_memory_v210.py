"""agent-memory 2.10: write rules (category, similarity check, expiry), candidate pool, superseded items, review
dates, read-size limits, the doctor health check, journal -> memory, and the migration of existing items."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory as m  # noqa: E402
import journal  # noqa: E402

CATS = {'categories': [
    {'id': 'profile', 'label': '称呼与沟通', 'core': True},
    {'id': 'rules', 'label': '长期规矩', 'core': True},
    {'id': 'env', 'label': '环境', 'review_days': 90},
    {'id': 'video', 'label': '视频制作'},
    {'id': 'demo', 'label': '演示项目'},
    {'id': 'legacy', 'label': '旧环境', 'legacy': True},
],
    'policy': {'candidate_pool': True, 'review_days': 180, 'limits': {'prefs_chars': 400, 'brief_head_chars': 900}},
    'doctor': {'stale_patterns': [{'pattern': 'old-sandbox', 'label': '旧沙箱'}],
               'auto_category': [{'category': 'demo', 'scope_prefixes': ['project:workflow/demo']}]}}


class V210(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name) / 'agent-memory'
        m.ensure_layout(self.r)
        self.cats(CATS)

    def tearDown(self):
        self.t.cleanup()

    def cats(self, data):
        (self.r / 'categories.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def pref(self, text, cat='video', **kw):
        f = {'text': text, 'source': kw.pop('source', 'explicit'), 'scope': kw.pop('scope', 'global'), 'evidence': 'e',
             'category': cat, **kw.pop('fields', {})}
        return m.upsert(self.r, 'preference', f, **kw)

    def lesson(self, title, rule='先查清楚再动手', cat='env', **kw):
        f = {'title': title, 'rule': rule, 'trigger': 'failure', 'scope': kw.pop('scope', 'global'), 'category': cat}
        return m.upsert(self.r, 'lesson', f, **kw)

    def item(self, iid):
        return m.find_item(self.r, iid)[2]

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = m.main(['--root', str(self.r), *args])
        return code, out.getvalue(), err.getvalue()

    # -- write rules ------------------------------------------------------------------------------------------
    def test_category_is_required_and_the_error_lists_the_choices(self):
        with self.assertRaises(m.MemError) as e:
            m.upsert(self.r, 'lesson', {'title': '没类别', 'rule': 'r', 'trigger': 'failure', 'scope': 'global'})
        self.assertIn('称呼与沟通', str(e.exception))
        code, _, err = self.cli('learn', '--title', '乱写', '--rule', 'r', '--category', 'nope')
        self.assertEqual(code, 1)
        self.assertIn('nope', err)
        self.assertEqual(m.load_items(self.r, 'lesson'), [])
        self.assertEqual(self.lesson('按标签选', cat='环境')['type'], 'lesson')  # a label works too

    def test_similar_item_is_not_saved_unless_forced_or_folded_in(self):
        a = self.lesson('隧道域名被代理改写成假地址', direct=True)
        near = self.lesson('隧道的域名被代理改写成了假地址', direct=True)
        self.assertEqual((near['action'], near['saved']), ('similar_found', False))
        self.assertEqual(near['similar'][0]['id'], a['id'])
        self.assertIn('--id', near['hint'])
        self.assertEqual(len(m.load_items(self.r, 'lesson')), 1)
        code, out, _ = self.cli('learn', '--title', '隧道的域名被代理改写成了假地址', '--rule', '先查清楚再动手',
                                '--category', 'env')
        self.assertEqual(code, 3)
        folded = self.lesson('隧道的域名被代理改写成了假地址', iid=a['id'][-6:])
        self.assertEqual((folded['action'], folded['count']), ('update', 2))
        forced = self.lesson('隧道域名又被代理改写成了假地址', force=True)
        self.assertEqual(forced['action'], 'add')
        same_rule = self.lesson('完全不同的标题', rule='先查清楚再动手', direct=True)
        self.assertEqual(same_rule['action'], 'similar_found')  # same rule under another title

    def test_expiry_and_archive(self):
        x = self.pref('这周的封面先用蓝色', direct=True, fields={'expires_at': m.expiry(days=3)})
        item = self.item(x['id'])
        self.assertIn('expires_at', item)
        self.assertNotIn('review_by', item)  # it expires on its own: no review date
        m.edit(self.r, x['id'], {'expires_at': '2020-01-01'})
        self.assertNotIn('这周的封面', m.brief(self.r))
        rep = m.doctor(self.r)
        self.assertEqual([e['id'] for e in rep['auto']['expired']], [x['id']])
        rep = m.doctor(self.r, fix=True)
        self.assertEqual(rep['fixed']['archived'], [x['id']])
        self.assertEqual(rep['counts']['archived'], 1)
        self.assertTrue(m.search(self.r, '这周的封面', archived=True))  # archived, not deleted

    # -- candidate pool ---------------------------------------------------------------------------------------
    def test_new_items_wait_in_the_pool_and_safe_ones_are_accepted_by_rule(self):
        safe = self.lesson('渲染前先清缓存', rule='渲染前清空缓存目录')
        core = self.pref('回复用中文', 'profile')
        inferred = self.pref('喜欢短句', 'video', source='inferred')
        old = self.lesson('old-sandbox 里的 /tmp 会丢', rule='别把产物放 tmp')
        direct = self.pref('删除前先问', 'rules', direct=True)
        self.assertEqual(self.item(safe['id'])['status'], 'candidate')
        self.assertEqual(self.item(direct['id'])['status'], 'active')
        b = m.brief(self.r)
        self.assertIn('渲染前先清缓存', b)
        self.assertIn('候选', b)
        p = m.prefs(self.r)
        self.assertIn('删除前先问', p)
        self.assertNotIn('回复用中文', p)  # candidates never go into another assistant's preference memory
        st = {x['id']: x for x in m.pool_status(self.r)}
        self.assertTrue(st[safe['id']]['safe'])
        self.assertFalse(st[core['id']]['safe'])
        self.assertFalse(st[inferred['id']]['safe'])
        self.assertIn('旧沙箱', '；'.join(st[old['id']]['why']))
        rep = m.pool(self.r, accept_safe=True)
        self.assertEqual(rep['accepted'], [safe['id']])
        self.assertEqual(self.item(safe['id'])['status'], 'active')
        rep = m.pool(self.r, accept=[core['id'][-6:]], why='用户点头')
        self.assertEqual(rep['accepted'], [core['id']])
        self.assertIn('回复用中文', m.prefs(self.r))
        with self.assertRaises(m.MemError):
            m.pool(self.r, drop=[old['id']])  # dropping needs a reason
        rep = m.pool(self.r, drop=[old['id']], reason='旧环境')
        self.assertEqual(self.item(old['id'])['status'], 'retired')
        self.assertEqual(rep['needs_user'], [inferred['id'][-6:]])
        code, out, _ = self.cli('pool')
        self.assertEqual(code, 0)
        self.assertIn('候选池 1 条', out)
        with self.assertRaises(m.MemError):
            m.pool(self.r, accept=[safe['id']])  # already formal

    def test_pool_off_saves_active(self):
        data = dict(CATS, policy={})
        self.cats(data)
        self.assertEqual(self.item(self.lesson('不开候选池')['id'])['status'], 'active')

    # -- superseded -------------------------------------------------------------------------------------------
    def test_supersede_keeps_the_old_item_and_hides_it(self):
        old = self.pref('默认旁白用音色01', 'profile', direct=True)
        new = self.pref('默认旁白改用音色02', 'profile', direct=True, supersedes=old['id'][-6:])
        self.assertEqual(new['superseded'], old['id'])
        o = self.item(old['id'])
        self.assertEqual((o['status'], o['superseded_by']), ('superseded', new['id']))
        self.assertEqual(self.item(new['id'])['supersedes'], [old['id']])
        self.assertNotIn('音色01', m.prefs(self.r))
        self.assertNotIn('音色01', m.brief(self.r))
        self.assertIn('音色01', m.prefs(self.r, superseded=True))
        self.assertIn('已取代', m.brief(self.r, superseded=True))
        with self.assertRaises(m.MemError):
            m.supersede(self.r, old['id'], new['id'])  # already superseded
        a = self.lesson('封面底色用黑', rule='封面纯黑底', direct=True)
        b = self.lesson('封面底色用纸白', rule='封面纸白网格底', direct=True, force=True)
        code, _, _ = self.cli('supersede', '--old', a['id'][-6:], '--new', b['id'][-6:], '--reason', '用户 11-10 定了纸白')
        self.assertEqual(code, 0)
        self.assertEqual(self.item(a['id'])['status'], 'superseded')
        self.assertTrue(m.validate(self.r)['pass'])
        self.assertIn('已取代', (self.r / 'MEMORY.md').read_text(encoding='utf-8'))

    # -- review dates -----------------------------------------------------------------------------------------
    def test_review_dates_overdue_and_review(self):
        e = self.lesson('代理要直连', direct=True)
        v = self.pref('字幕白字黑边', direct=True)
        lg = self.lesson('旧沙箱的快照规则', rule='快照前停服务', cat='legacy', direct=True)
        today = m.today()
        self.assertEqual(self.item(e['id'])['review_by'], (today + m.datetime.timedelta(days=90)).isoformat())
        self.assertEqual(self.item(v['id'])['review_by'], (today + m.datetime.timedelta(days=180)).isoformat())
        self.assertNotIn('review_by', self.item(lg['id']))  # legacy: never asks for a review
        m.edit(self.r, e['id'], {'review_by': '2020-01-01'})
        rep = m.doctor(self.r)
        self.assertEqual([x['id'] for x in rep['user']['review_overdue']], [e['id']])
        self.assertIn('超期没复核（1 条）', m.doctor_text(rep))
        res = m.review(self.r, [e['id'][-6:]], days=30)
        self.assertEqual(res['items'][0]['review_by'], (today + m.datetime.timedelta(days=30)).isoformat())
        self.assertEqual(m.doctor(self.r)['user']['review_overdue'], [])

    def test_migrate_fills_review_dates_without_losing_anything(self):
        self.cats({'categories': CATS['categories']})  # old repo: no policy yet, no review dates
        ids = [self.lesson(t, rule=r, cat=c)['id'] for t, r, c in [
            ('代理改写隧道域名', '隧道走直连', 'env'), ('字幕压住了人脸', '字幕放底部安全区', 'video'),
            ('旧机器快照丢配置', '快照前先停服务', 'legacy')]]
        for iid in ids:
            m.edit(self.r, iid, {'review_by': ''})
        m.retire(self.r, ids[1], '测试')
        before = {x['id']: x for x in m.load_items(self.r, 'lesson')}
        res = m.migrate(self.r)
        self.assertEqual(res['review_dated'], [ids[0]])  # retired and legacy items are left alone
        after = {x['id']: x for x in m.load_items(self.r, 'lesson')}
        self.assertEqual(set(before), set(after))
        for iid in ids:
            for k, v in before[iid].items():
                self.assertEqual(after[iid][k], v)
        self.assertEqual(m.migrate(self.r)['count'], 0)  # idempotent
        self.assertTrue(m.validate(self.r)['pass'])

    # -- doctor -----------------------------------------------------------------------------------------------
    def test_doctor_reports_and_fixes_only_the_safe_part(self):
        a = self.lesson('完全相同的坑', direct=True)
        again = self.lesson('完全相同的坑', direct=True)
        self.assertEqual((again['action'], again['id']), ('update', a['id']))  # same title = same lesson, counted
        raw = dict(self.item(a['id']), id='lesson-20200101T000000Z-abcdef', count=1,
                   created_at='2099-01-01T00:00:00+00:00')  # an exact duplicate left over from older data
        (self.r / m.KINDS['lesson'] / f"{raw['id']}.json").write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        b = {'id': raw['id']}
        pend = self.pref('待用户定的说法', fields={'tags': [m.REVIEW_TAG, 'conflict-x']}, direct=True)
        stale = self.lesson('old-sandbox 路径别再用', rule='用新路径', direct=True)
        cand = self.lesson('候选里的一条', rule='候选规则不一样')
        no_cat = m.upsert(self.r, 'lesson', {'title': '项目里的坑', 'rule': '项目规则', 'trigger': 'failure',
                                             'scope': 'project:workflow/demo', 'category': 'env'}, direct=True)
        f = self.r / m.KINDS['lesson'] / f"{no_cat['id']}.json"
        raw = json.loads(f.read_text(encoding='utf-8'))
        raw.pop('category')
        f.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')  # an item from before the taxonomy
        rep = m.doctor(self.r)
        self.assertEqual(len(rep['auto']['identical']), 1)
        self.assertEqual(rep['auto']['category'][0]['suggested'], 'demo')
        self.assertIn('conflict-x', rep['user']['needs_review'])
        self.assertEqual([x['id'] for x in rep['user']['stale_mentions']], [stale['id']])
        self.assertEqual([x['id'] for x in rep['pool']], [cand['id']])
        text = m.doctor_text(rep)
        for part in ('可自动处理', '候选池', '每次都读的部分', '已标待确认', '矛盾候选', '高度相似', '提到旧环境',
                     '超期没复核', '类别拿不准'):
            self.assertIn(part, text)
        rep = m.doctor(self.r, fix=True)
        self.assertEqual(rep['fixed']['merged'], [{'from': b['id'], 'into': a['id']}])
        self.assertEqual(rep['fixed']['categorized'], [{'id': no_cat['id'], 'category': 'demo'}])
        self.assertEqual(self.item(a['id'])['count'], 3)
        self.assertEqual(self.item(cand['id'])['status'], 'candidate')  # --fix never accepts candidates
        self.assertIn(m.REVIEW_TAG, self.item(pend['id'])['tags'])  # nor touches what the user must decide
        self.assertEqual(self.item(stale['id'])['status'], 'active')
        code, out, _ = self.cli('doctor', '--json')
        self.assertEqual(code, 0)
        self.assertIn('sizes', json.loads(out))
        rep = m.doctor(self.r, mark_review=True)
        self.assertIn('stale-mention', self.item(stale['id'])['tags'])

    def test_read_size_limits_name_what_to_move(self):
        for i in range(6):
            self.pref(f'第{i}条很长的核心偏好' + '细节' * 40 + str(i * 97), 'profile', direct=True, force=True)
            self.pref(f'第{i}条很长的视频偏好' + '参数' * 40 + str(i * 89), 'video', direct=True, force=True)
        sizes = {s['what']: s for s in m.read_sizes(self.r)}
        self.assertGreater(sizes['prefs']['chars'], 400)
        self.assertTrue(sizes['prefs']['move'])
        self.assertTrue(sizes['brief']['move'])
        self.assertTrue(all(x['category'] == 'video' for x in sizes['brief']['move']))
        self.assertIn('超了', m.doctor_text(m.doctor(self.r)))
        self.assertIn('超过上限', m.brief(self.r))

    # -- journal -> memory ------------------------------------------------------------------------------------
    def test_journal_line_becomes_a_candidate_with_its_source(self):
        prog = Path(self.t.name) / 'progress'
        P = 'workflow/demo'
        journal.add(prog, P, '代理把隧道域名改成假地址，要直连', 'found', '2026-11-09', '10:00', 'UTC')
        r = journal.add(prog, P, '以后封面统一纸白底', 'decision', '2026-11-09', '11:00', 'UTC')
        self.assertIn('from-journal', r['promote'])
        self.assertEqual(len(journal.candidates(prog, P, date='2026-11-09')), 2)
        code, out, _ = self.cli('from-journal', '--project', P, '--date', '2026-11-09', '--entry', '1', '--as', 'lesson',
                                '--category', 'env', '--rule', '隧道域名走直连', '--progress-root', str(prog))
        self.assertEqual(code, 0, out)
        res = json.loads(out)
        it = self.item(res['id'])
        self.assertEqual((it['status'], it['source_date'], it['source_journal']),
                         ('candidate', '2026-11-09', 'workflow/demo@2026-11-09#1'))
        self.assertEqual(len(journal.candidates(prog, P, date='2026-11-09')), 1)
        code, _, err = self.cli('promote', '--project', P, '--date', '2026-11-09', '--entry', '1', '--as', 'lesson',
                                '--category', 'env', '--rule', 'x', '--progress-root', str(prog))
        self.assertEqual(code, 1)  # already saved; old command name still works
        self.assertIn('已经存进记忆', err)


if __name__ == '__main__':
    unittest.main()
