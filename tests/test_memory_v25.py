"""v2.5 memory refinements: edit vs. occurrence counting, similarity merge/warn, merge, priorities,
short ids, inferred observation counts, duplicate report."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory as m  # noqa: E402


class Refinements(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def lesson(self, title, rule='r', scope='global'):
        return m.upsert(self.r, 'lesson', {'title': title, 'rule': rule, 'trigger': 'failure', 'scope': scope})

    def test_edit_never_counts_as_an_occurrence(self):
        a = self.lesson('递归遍历长历史崩溃')
        res = m.edit(self.r, a['id'], {'scope': 'domain:workflow', 'source_projects': 'workflow/x, workflow/y'}, why='re-scope')
        item = m.find_item(self.r, a['id'])[2]
        self.assertEqual((item['count'], item['scope'], item['source_projects']), (1, 'domain:workflow', ['workflow/x', 'workflow/y']))
        self.assertEqual(res['fields'], ['scope', 'source_projects'])
        self.assertIn('edit', (self.r / 'CHANGELOG.md').read_text(encoding='utf-8'))
        with self.assertRaises(m.MemError):
            m.edit(self.r, a['id'], {'count': 9})  # counts are not editable
        with self.assertRaises(m.MemError):
            m.edit(self.r, a['id'], {'scope': 'everywhere'})
        self.assertEqual(self.lesson('递归遍历长历史崩溃')['count'], 2)  # a real recurrence still counts

    def test_similar_wording_merges_or_warns(self):
        a = self.lesson('沙箱重启后 /tmp 被清空')
        same = self.lesson('沙箱重启后/tmp被清空！')
        self.assertEqual((same['action'], same['merged_by_similarity'], same['count']), ('update', a['id'], 2))
        near = self.lesson('沙箱重启后 /tmp 目录被清空了')
        self.assertEqual(near['action'], 'add')
        self.assertEqual(near['similar'][0]['id'], a['id'])
        dupes = m.validate(self.r)['possible_duplicates']
        self.assertEqual({(d['a'], d['b']) for d in dupes} & {(a['id'], near['id']), (near['id'], a['id'])} != set(), True)
        same_rule = self.lesson('完全不同的标题', rule='会话辅助脚本放 /tmp，丢了就重建')
        self.lesson('另一个说法', rule='会话辅助脚本放 /tmp，丢了就按需重建')
        self.assertTrue(same_rule['action'] == 'add')
        warn = self.lesson('第三个说法', rule='会话辅助脚本放 /tmp，丢了就按需重建吧')
        self.assertTrue(any(s['similarity'] >= 0.3 for s in warn.get('similar', [])))  # same rule, other title

    def test_merge_folds_duplicates(self):
        a = self.lesson('素材站下载被限流')
        m.upsert(self.r, 'lesson', {'title': '素材站下载被限流', 'rule': 'r', 'trigger': 'failure', 'scope': 'global'})
        b = self.lesson('素材网站下载时被限流', rule='换可授权素材')
        res = m.merge(self.r, b['id'][-6:], a['id'][-6:], why='同一个坑')
        self.assertEqual(res['count'], 3)
        self.assertEqual(m.find_item(self.r, b['id'])[2]['status'], 'retired')
        self.assertEqual(len(m.active(self.r, 'lesson')), 1)
        with self.assertRaises(m.MemError):
            m.merge(self.r, a['id'], a['id'])

    def test_priorities_short_ids_and_observations(self):
        crit = m.upsert(self.r, 'preference', {'text': '每次回复都给选项', 'source': 'explicit', 'scope': 'global',
                                               'evidence': '原话', 'priority': 1})
        m.upsert(self.r, 'preference', {'text': '常规操作不用请示', 'source': 'explicit', 'scope': 'global',
                                        'evidence': 'GLOBAL', 'priority': 3})
        m.upsert(self.r, 'preference', {'text': '回复用中文', 'source': 'inferred', 'scope': 'global', 'evidence': '一直用中文'})
        again = m.upsert(self.r, 'preference', {'text': '回复用中文', 'source': 'inferred', 'scope': 'global', 'evidence': '又一次'})
        self.assertEqual(m.find_item(self.r, again['id'])[2]['observations'], 2)
        text = m.brief(self.r)
        prefs = text.split('## 偏好')[1].split('##')[0]
        self.assertTrue(prefs.split('\n')[1].startswith('- ★ 每次回复都给选项'))
        self.assertIn(f"· {crit['id'][-6:]}）", prefs)
        self.assertIn('推断 ×2', prefs)
        self.assertNotIn('常规操作不用请示', prefs)
        self.assertIn('另有 1 条背景偏好', prefs)
        self.assertIn('常规操作不用请示', m.brief(self.r, full=True))
        m.retire(self.r, crit['id'][-6:], 'test via short id')
        self.assertNotIn('每次回复都给选项', m.brief(self.r))
        with self.assertRaises(m.MemError):
            m.find_item(self.r, crit['id'][-3:])  # suffixes shorter than 4 characters are refused
        with self.assertRaises(m.MemError):
            m.upsert(self.r, 'preference', {'text': 'x', 'source': 'explicit', 'scope': 'global', 'evidence': 'e', 'priority': 5})


if __name__ == '__main__':
    unittest.main()
