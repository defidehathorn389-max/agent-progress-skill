"""Layered work journal: day files, month/year rollups, recent reading order, slim exclusion of old days."""
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import handoff  # noqa: E402
import journal  # noqa: E402
import progress_sync as ps  # noqa: E402

P = 'workflow/demo'


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='journal-test-'))
        (self.root / 'projects' / 'workflow' / 'demo').mkdir(parents=True)

    def test_add_and_key_create_day_file(self):
        r = journal.add(self.root, P, 'built the thing', 'done', '2026-10-30', '09:05', 'UTC')
        self.assertEqual(r['entries'], 1)
        journal.add(self.root, P, 'chose plan B because A leaks', 'decision', '2026-10-30', '10:00', 'UTC')
        journal.key(self.root, P, 'thing built; plan B chosen', '2026-10-30', 'UTC')
        text = (self.root / 'projects/workflow/demo/journal/days/2026-10-30.md').read_text(encoding='utf-8')
        self.assertIn('- 09:05 [完成] built the thing', text)
        self.assertIn('- 10:00 [决定] chose plan B', text)
        self.assertLess(text.index('thing built; plan B chosen'), text.index('## 记录'))

    def test_secret_rejected(self):
        with self.assertRaises(journal.JournalError):
            journal.add(self.root, P, 'token ghp_' + 'a' * 30, 'note', '2026-10-30', '09:00', 'UTC')

    def test_rollup_keeps_written_summary(self):
        journal.add(self.root, P, 'a', 'done', '2026-10-01', '09:00', 'UTC')
        journal.key(self.root, P, 'day one essentials', '2026-10-01', 'UTC')
        journal.add(self.root, P, 'b', 'done', '2026-10-02', '09:00', 'UTC')
        r = journal.rollup_month(self.root, P, '2026-10')
        self.assertEqual(r['days'], 2)
        f = self.root / 'projects/workflow/demo/journal/months/2026-10.md'
        t = f.read_text(encoding='utf-8')
        self.assertIn('day one essentials', t)
        self.assertIn('- 09:00 [完成] b', t)  # no key points -> first log lines
        f.write_text(t.replace(journal.SUM_TODO, 'October: shipped a and b.'), encoding='utf-8')
        journal.add(self.root, P, 'c', 'done', '2026-10-03', '09:00', 'UTC')
        journal.rollup_month(self.root, P, '2026-10')
        t2 = f.read_text(encoding='utf-8')
        self.assertIn('October: shipped a and b.', t2)
        self.assertIn('### 2026-10-03', t2)
        y = journal.rollup_year(self.root, P, '2026')
        self.assertEqual(y['months'], 1)
        self.assertIn('October: shipped a and b.',
                      (self.root / 'projects/workflow/demo/journal/years/2026.md').read_text(encoding='utf-8'))

    def test_recent_reads_year_month_then_newest_days(self):
        for d in ('2026-09-28', '2026-10-01', '2026-10-05', '2026-10-06', '2026-10-07'):
            journal.add(self.root, P, 'work ' + d, 'done', d, '09:00', 'UTC')
        journal.rollup_month(self.root, P, '2026-09')
        journal.rollup_month(self.root, P, '2026-10')
        journal.rollup_year(self.root, P, '2026')
        out = journal.recent(self.root, P, days=3, tz='UTC')
        self.assertLess(out.index('年度汇总'), out.index('月度汇总'))
        self.assertLess(out.index('月度汇总'), out.index('日记录'))
        self.assertIn('日记录 projects/workflow/demo/journal/days/2026-10-07.md', out)
        self.assertNotIn('日记录 projects/workflow/demo/journal/days/2026-10-01.md', out)
        self.assertNotIn('月度汇总 projects/workflow/demo/journal/months/2026-09.md', out)
        self.assertIn('更早的 2 天', out)

    def test_check_flags_unsummarised_past_month(self):
        journal.add(self.root, P, 'x', 'done', '2020-01-02', '09:00', 'UTC')
        todo = journal.check(self.root, P, 'UTC')
        self.assertTrue(any('2020-01' in t for t in todo))
        journal.rollup_month(self.root, P, '2020-01')
        todo = journal.check(self.root, P, 'UTC')
        self.assertTrue(any('2020-01' in t and '总结' in t for t in todo))
        self.assertTrue(any('2020' in t and '年度' in t for t in todo))

    def test_bad_project_rejected(self):
        with self.assertRaises(handoff.HandoffError):
            journal.add(self.root, '../x', 't', 'done', '2026-10-30', '09:00', 'UTC')


class JournalSlimTest(unittest.TestCase):
    def test_old_day_files_excluded_summaries_kept(self):
        now = datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc)
        base = 'projects/workflow/demo/journal/'
        days = [base + f'days/2026-09-{d:02d}.md' for d in (1, 2, 3, 4, 5)] + [base + 'days/2026-10-09.md']
        other = 'projects/workflow/old/journal/days/2025-01-01.md'
        paths = days + [other, base + 'months/2026-09.md', base + 'years/2026.md']
        pats, excluded = ps.slim_patterns(paths, days=3, now=now)
        self.assertIn('!/' + base + 'days/2026-09-01.md', pats)
        self.assertIn('!/' + base + 'days/2026-09-02.md', pats)
        self.assertIn('!/' + base + 'days/2026-09-03.md', pats)
        self.assertNotIn('!/' + base + 'days/2026-09-04.md', pats)  # newest 3 per project always kept
        self.assertNotIn('!/' + base + 'days/2026-10-09.md', pats)
        self.assertNotIn('!/' + other, pats)
        self.assertFalse(any('months/' in p or 'years/' in p for p in pats))
        self.assertEqual(excluded, 3)

    def test_days_zero_keeps_all_days(self):
        pats, excluded = ps.slim_patterns(['projects/a/b/journal/days/2000-01-01.md'], days=0)
        self.assertEqual(excluded, 0)


if __name__ == '__main__':
    unittest.main()
