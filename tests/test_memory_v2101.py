"""agent-memory 2.10.1: brief hides old-environment (legacy) items; doctor --dismiss records that two items do not
contradict, so the pair is no longer reported (both directions)."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory as m  # noqa: E402

CATS = {'categories': [
    {'id': 'profile', 'label': '称呼与沟通', 'core': True},
    {'id': 'rules', 'label': '长期规矩', 'core': True},
    {'id': 'env', 'label': '环境', 'review_days': 90},
    {'id': 'legacy', 'label': '旧环境', 'legacy': True},
],
    'policy': {'candidate_pool': False, 'review_days': 180},
    'doctor': {'stale_patterns': [{'pattern': '桥接', 'label': '旧桥接说法'}]}}


class V2101(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name) / 'agent-memory'
        m.ensure_layout(self.r)
        (self.r / 'categories.json').write_text(json.dumps(CATS, ensure_ascii=False), encoding='utf-8')

    def tearDown(self):
        self.t.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = m.main(['--root', str(self.r), *args])
        return code, out.getvalue(), err.getvalue()

    def lesson(self, title, rule, cat='env'):
        return m.upsert(self.r, 'lesson', {'title': title, 'rule': rule, 'trigger': 'failure', 'scope': 'global',
                                           'category': cat}, force=True)

    # -- brief hides legacy ---------------------------------------------------------------------------------
    def test_brief_hides_legacy_facts_prefs_and_lessons_but_search_finds_them(self):
        m.upsert(self.r, 'fact', {'text': '旧沙箱只保留快照目录', 'evidence': 'e', 'category': 'legacy'})
        m.upsert(self.r, 'fact', {'text': '用户电脑有 RTX 3060', 'evidence': 'e', 'category': 'env'})
        m.upsert(self.r, 'preference', {'text': '旧桥接的地址问用户要', 'source': 'explicit', 'scope': 'global',
                                        'evidence': 'e', 'category': 'legacy'})
        self.lesson('旧沙箱重启清空 tmp', '产物放常驻区', cat='legacy')
        self.lesson('代理改写隧道域名', '先加直连规则')
        out = m.brief(self.r)
        self.assertIn('RTX 3060', out)
        self.assertIn('代理改写隧道域名', out)
        for old in ('快照目录', '旧桥接的地址', '重启清空 tmp'):
            self.assertNotIn(old, out)
        self.assertIn('旧环境类 3 条不在摘要里', out)
        code, found, _ = self.cli('search', '快照目录')
        self.assertEqual(code, 0)
        self.assertIn('快照目录', found)

    def test_brief_without_legacy_items_has_no_legacy_line(self):
        m.upsert(self.r, 'fact', {'text': '用户电脑有 RTX 3060', 'evidence': 'e', 'category': 'env'})
        self.assertNotIn('旧环境类', m.brief(self.r))

    # -- doctor --dismiss -----------------------------------------------------------------------------------
    def pair(self):
        a = self.lesson('封面用深蓝网格底', '所有封面都用深蓝网格底加橙色标签')
        b = self.lesson('封面避免网格底', '所有封面避免深蓝网格底加橙色标签')
        return a['id'], b['id']

    def contra_ids(self):
        return [{d['a']['id'], d['b']['id']} for d in m.doctor(self.r)['user']['contradictions']]

    def test_dismiss_stops_reporting_the_pair_both_directions(self):
        a, b = self.pair()
        self.assertIn({a, b}, self.contra_ids())
        code, out, _ = self.cli('doctor', '--dismiss', b[-6:], a[-6:])
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertEqual(sorted(res['changed']), sorted([a, b]))
        self.assertNotIn({a, b}, self.contra_ids())
        for x, y in ((a, b), (b, a)):
            nc = m.find_item(self.r, x)[2]['not_contradicting']
            self.assertEqual([d['id'] for d in nc], [y])
            self.assertEqual(nc[0]['by'], '用户')
            self.assertRegex(nc[0]['date'], r'^\d{4}-\d{2}-\d{2}$')
        again = m.dismiss(self.r, a, b)  # idempotent
        self.assertEqual(again['changed'], [])
        m.validate(self.r)  # raises on a bad item

    def test_one_sided_record_is_enough(self):
        a, b = self.pair()
        kind, f, x = m.find_item(self.r, a)
        x['not_contradicting'] = [{'id': b, 'by': '用户', 'date': '2026-11-10'}]
        f.write_text(json.dumps(x, ensure_ascii=False), encoding='utf-8')
        self.assertNotIn({a, b}, self.contra_ids())

    def test_dismiss_rejects_same_item_and_bad_field(self):
        a, _ = self.pair()
        with self.assertRaises(m.MemError):
            m.dismiss(self.r, a, a)
        kind, f, x = m.find_item(self.r, a)
        with self.assertRaises(m.MemError):
            m.validate_item(dict(x, not_contradicting=[{'id': 'nope'}]), kind)


if __name__ == '__main__':
    unittest.main()
