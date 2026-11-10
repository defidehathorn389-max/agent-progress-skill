"""agent-memory categories: optional taxonomy, grouped brief/MEMORY.md, clean prefs output, review tag."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory as m  # noqa: E402

CATS = {'categories': [
    {'id': 'profile', 'label': '称呼与沟通', 'core': True},
    {'id': 'rules', 'label': '长期规矩', 'core': True},
    {'id': 'env', 'label': '环境', 'desc': '机器与网络'},
    {'id': 'video', 'label': '视频制作'},
]}


class Categories(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name) / 'agent-memory'
        m.ensure_layout(self.r)

    def tearDown(self):
        self.t.cleanup()

    def pref(self, text, cat=None, **kw):
        f = {'text': text, 'source': kw.get('source', 'explicit'), 'scope': kw.get('scope', 'global'),
             'evidence': 'e', 'priority': kw.get('priority', 2), 'tags': kw.get('tags', [])}
        if cat:
            f['category'] = cat
        return m.upsert(self.r, 'preference', f)

    def write_cats(self, data):
        (self.r / 'categories.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_without_taxonomy_nothing_changes_and_category_is_refused(self):
        self.pref('回复用中文')
        self.assertIn('## 偏好（', m.brief(self.r))
        with self.assertRaises(m.MemError):
            self.pref('另一条', 'profile')
        self.assertNotIn('uncategorized', m.validate(self.r))
        self.assertIn('回复用中文', m.prefs(self.r))

    def test_grouped_brief_prefs_and_views(self):
        # an item saved before the taxonomy existed stays uncategorized
        m.upsert(self.r, 'lesson', {'title': '没分类的坑', 'rule': '先查', 'trigger': 'failure', 'scope': 'global'})
        self.write_cats(CATS)
        a = self.pref('回复用中文', 'profile', source='inferred')
        self.pref('删除前先问', 'rules', priority=3)
        self.pref('封面出三种尺寸', 'video')
        self.pref('旧环境说明', 'env', tags=[m.REVIEW_TAG])
        m.upsert(self.r, 'lesson', {'title': '代理改写域名', 'rule': '隧道走直连', 'trigger': 'failure', 'scope': 'global',
                                    'category': 'env'})
        with self.assertRaises(m.MemError):  # with a taxonomy, a new item must name a category
            m.upsert(self.r, 'lesson', {'title': '又一个没分类的坑', 'rule': '先查', 'trigger': 'failure', 'scope': 'global'})
        with self.assertRaises(m.MemError):
            self.pref('乱写类别', 'nope')
        b = m.brief(self.r)
        self.assertLess(b.index('### 称呼与沟通'), b.index('## 其他偏好'))
        self.assertLess(b.index('## 其他偏好'), b.index('### 视频制作'))
        self.assertIn('旧环境说明（待用户确认）', b)
        self.assertIn('1 条标了「待用户确认」', b)
        self.assertIn('### 未分类', b)  # the uncategorized lesson
        self.assertNotIn('删除前先问', b)  # priority 3 stays background in brief ...
        p = m.prefs(self.r)
        self.assertIn('删除前先问', p)  # ... but prefs lists every priority of the core categories
        self.assertIn('回复用中文（推断）', p)
        self.assertNotIn('封面', p)
        self.assertNotIn('旧环境', p)
        self.assertIn(a['id'][-6:], m.prefs(self.r, ids=True))
        view = (self.r / 'MEMORY.md').read_text(encoding='utf-8')
        self.assertIn('**环境**（`env`）：偏好 1 · 事实 0 · 教训 1 — 机器与网络', view)
        self.assertIn('### 长期规矩（1）', view)
        v = m.validate(self.r)
        self.assertEqual(len(v['uncategorized']), 1)
        self.assertEqual(len(v['needs_review']), 1)
        self.assertTrue(v['views_current'])
        lesson = [x for x in m.active(self.r, 'lesson') if not x.get('category')][0]
        m.edit(self.r, lesson['id'], {'category': 'video'})
        self.assertEqual(m.validate(self.r)['uncategorized'], [])

    def test_bad_taxonomy_and_unknown_category_fail_validate(self):
        self.write_cats({'categories': [{'id': 'A b', 'label': 'x'}]})
        with self.assertRaises(m.MemError):
            m.load_categories(self.r)
        self.write_cats(CATS)
        self.pref('回复用中文', 'profile')
        self.write_cats({'categories': CATS['categories'][1:]})
        with self.assertRaises(m.MemError):
            m.validate(self.r)

    def test_cli_prefs(self):
        self.write_cats(CATS)
        rc = m.main(['--root', str(self.r), 'prefer', '--text', '叫我老板', '--category', 'profile'])
        self.assertEqual(rc, 0)
        self.assertEqual(m.active(self.r, 'preference')[0]['category'], 'profile')
        self.assertEqual(m.main(['--root', str(self.r), 'prefs']), 0)


if __name__ == '__main__':
    unittest.main()
