"""v2.3: context summary in resume, find (vague references), recent (cross-project timeline), INDEX active block."""
import datetime
import itertools
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import handoff as h  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name)
        start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        tick = itertools.count()
        self.clock = mock.patch.object(h, 'now', lambda: (start + datetime.timedelta(minutes=next(tick))).isoformat(timespec='seconds'))
        self.clock.start()
        harbor = h.new_state('海港城市：山海之城', '纪录片风格的原创地理科普', next_actions=['等待反馈'])
        harbor['handoff']['waiting_for'] = '用户审片'
        self.harbor = h.checkpoint(self.r, 'video/harbor', harbor, 'NEW', '创建海港项目')
        stone = h.new_state('石灰岩地貌：石头森林', '地理科普纪录短片', status='COMPLETED')
        stone['context'] = {'aliases': ['石林', 'stone-forest'], 'details': {'release': 'v4', 'minutes': 7.5}}
        self.stone = h.checkpoint(self.r, 'video/stone', stone, 'NEW', '导入石灰岩项目')
        self.harbor2 = h.update(self.r, 'video/harbor', {'set': {'goal': '纪录片风格的原创地理科普（v2 实拍）'}},
                            self.harbor['checkpoint_id'], '加入实拍素材')

    def tearDown(self):
        self.clock.stop()
        self.t.cleanup()


class Find(Base):
    def test_ranking_and_and_semantics(self):
        top = h.find(self.r, '海港')[0]
        self.assertEqual((top['project'], top['matched']), ('video/harbor', ['notes', 'title']))  # title + creation note
        self.assertEqual([x['project'] for x in h.find(self.r, '石林')], ['video/stone'])  # alias in context
        self.assertEqual([x['project'] for x in h.find(self.r, '地理科普')], ['video/harbor', 'video/stone'])
        self.assertEqual(h.find(self.r, '海港 石林'), [])  # all terms must match
        self.assertEqual(h.find(self.r, '实拍')[0]['matched'], ['goal', 'notes'])
        with self.assertRaises(h.HandoffError):
            h.find(self.r, '   ')


class Recent(Base):
    def test_cross_project_timeline(self):
        rows = h.recent(self.r, 3)
        self.assertEqual([(r['project'], r['revision']) for r in rows],
                         [('video/harbor', 2), ('video/stone', 1), ('video/harbor', 1)])
        self.assertEqual(rows[0]['note'], '加入实拍素材')


class ResumeAndIndex(Base):
    def test_context_summary(self):
        brief = h.resume_text(self.r, 'video/stone')
        self.assertIn('## 项目上下文', brief)
        self.assertIn('- aliases：列表 2 项', brief)
        self.assertIn('- details：2 项 — minutes、release', brief)  # stored with sorted keys
        self.assertNotIn('## 项目上下文', h.resume_text(self.r, 'video/harbor'))

    def test_active_block_first(self):
        index = (self.r / 'INDEX.md').read_text(encoding='utf-8')
        self.assertIn('## 进行中（1）', index)
        active = index.split('## 进行中（1）')[1].split('## 全部项目')[0]
        self.assertIn('`video/harbor` · **IN_PROGRESS**', active)
        self.assertIn('⏳ 用户审片', active)
        self.assertNotIn('video/stone', active)
        self.assertEqual(set(h.retained_index_rows(self.r)), {'video/harbor', 'video/stone'})  # parser unaffected
        self.assertTrue(h.validate(self.r)['index_current'])


if __name__ == '__main__':
    unittest.main()
