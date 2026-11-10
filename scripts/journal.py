#!/usr/bin/env python3
"""Layered work journal for one project: day -> month -> year (stdlib only).

Layout (inside the progress repository, next to the checkpoints):
  projects/<domain>/<project>/journal/days/YYYY-MM-DD.md   one file per working day (what was done, decided, found)
  projects/<domain>/<project>/journal/months/YYYY-MM.md     month summary: an auto list of each day's key points
                                                            + a hand-written summary
  projects/<domain>/<project>/journal/years/YYYY.md         year summary: an auto list of each month's summary
                                                            + a hand-written summary
Checkpoints stay the authoritative *state*; the journal is the readable *history*. A slim clone checks out
only the recent day files (progress_sync.py clone --slim --recent-days N) plus all month and year files, so a
new agent pulls the recent days and the summaries instead of the whole history.

Commands:
  add     --project D/P --text T [--kind done|decision|found|issue|next|note] [--date YYYY-MM-DD] [--time HH:MM]
          append one line to that day's file (created on first use)
  key     --project D/P --text T [--date …]        add a line to the day's key points (the one-line essentials)
  recent  --project D/P [--days 3]                  print the year summary, the month summary and the newest N day
                                                    files that exist (the reading order for a hand-over)
  rollup  --project D/P [--month YYYY-MM | --year YYYY]
          regenerate the AUTO block of a month (from day key points) or year (from month summaries);
          the hand-written part between the summary markers is kept
  check   --project D/P                             list finished months/years still lacking a written summary
  list    --project D/P                             list journal files
Time zone: --tz, else env AGENT_TZ, else the machine's local zone.
Writes are local files: publish them with progress_sync.py push --project D/P (stages the whole project dir).
"""
import argparse, datetime, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff  # noqa: E402

KINDS = ('done', 'decision', 'found', 'issue', 'next', 'note')
KIND_LABEL = {'done': '完成', 'decision': '决定', 'found': '发现', 'issue': '问题', 'next': '下一步', 'note': '备注'}
DAY_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})\.md$')
MONTH_RE = re.compile(r'^(\d{4})-(\d{2})\.md$')
YEAR_RE = re.compile(r'^(\d{4})\.md$')
AUTO_BEGIN, AUTO_END = '<!-- AUTO:BEGIN（rollup 生成，勿手改） -->', '<!-- AUTO:END -->'
SUM_BEGIN, SUM_END = '<!-- SUMMARY:BEGIN -->', '<!-- SUMMARY:END -->'
SUM_TODO = '（待写：本期做成了什么、关键决定和原因、遗留问题。）'


class JournalError(Exception):
    pass


def tzinfo(name=None):
    name = name or os.environ.get('AGENT_TZ')
    if not name:
        return datetime.datetime.now().astimezone().tzinfo
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as e:  # noqa
        raise JournalError(f'unknown time zone {name!r}: {e}')


def today(tz=None):
    return datetime.datetime.now(tzinfo(tz))


def jdir(root, project):
    return handoff.project_dir(Path(root), project) / 'journal'


def _check_text(text):
    text = ' '.join(str(text).split())
    if not text:
        raise JournalError('empty text')
    if handoff.SECRET_RE.search(text):
        raise JournalError('potential plaintext credential in text (value redacted); journal entries never hold secrets')
    return text


def _parse_date(s):
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise JournalError(f'bad date {s!r}, want YYYY-MM-DD')


def day_path(root, project, d):
    return jdir(root, project) / 'days' / f'{d.isoformat()}.md'


def _new_day(project, d):
    return f'# {d.isoformat()} 工作记录 · `{project}`\n\n## 要点\n\n## 记录\n'


def _sections(text):
    """Split a day file into (head, key_lines, log_lines, tail_after_log)."""
    m1 = text.find('\n## 要点')
    m2 = text.find('\n## 记录')
    if m1 < 0 or m2 < 0 or m2 < m1:
        raise JournalError('day file lost its "## 要点" / "## 记录" headings; fix it by hand')
    head = text[:m1 + 1]
    key = [l for l in text[m1 + 1:m2].splitlines()[1:] if l.strip()]
    log = [l for l in text[m2 + 1:].splitlines()[1:] if l.strip()]
    return head, key, log


def _write_day(p, head, key, log):
    body = head + '## 要点\n\n' + ''.join(l + '\n' for l in key) + ('\n' if key else '') + '## 记录\n\n' + ''.join(l + '\n' for l in log)
    p.parent.mkdir(parents=True, exist_ok=True)
    handoff.atomic(p, body.encode('utf-8'))


def _load_day(root, project, d):
    p = day_path(root, project, d)
    text = p.read_text(encoding='utf-8') if p.exists() else _new_day(project, d)
    return p, _sections(text)


def add(root, project, text, kind='done', date=None, time=None, tz=None):
    if kind not in KINDS:
        raise JournalError(f'kind must be one of {", ".join(KINDS)}')
    now = today(tz)
    d = _parse_date(date) if date else now.date()
    t = time or now.strftime('%H:%M')
    if not re.fullmatch(r'\d{2}:\d{2}', t):
        raise JournalError('time must be HH:MM')
    p, (head, key, log) = _load_day(root, project, d)
    log.append(f'- {t} [{KIND_LABEL[kind]}] {_check_text(text)}')
    _write_day(p, head, key, log)
    return {'status': 'ADDED', 'file': str(p.relative_to(Path(root))), 'entries': len(log)}


def key(root, project, text, date=None, tz=None):
    d = _parse_date(date) if date else today(tz).date()
    p, (head, keys, log) = _load_day(root, project, d)
    keys.append(f'- {_check_text(text)}')
    _write_day(p, head, keys, log)
    return {'status': 'KEY_ADDED', 'file': str(p.relative_to(Path(root))), 'key_points': len(keys)}


def _files(root, project, sub, rx):
    d = jdir(root, project) / sub
    return sorted(f for f in d.iterdir() if rx.match(f.name)) if d.is_dir() else []


def day_files(root, project):
    return _files(root, project, 'days', DAY_RE)


def _key_points(f, fallback=3):
    _, keys, log = _sections(f.read_text(encoding='utf-8'))
    if keys:
        return keys
    return [l for l in log][:fallback] + ([f'- …当天另有 {len(log) - fallback} 条记录'] if len(log) > fallback else [])


def _summary_of(text):
    a, b = text.find(SUM_BEGIN), text.find(SUM_END)
    if a < 0 or b < 0:
        return ''
    s = text[a + len(SUM_BEGIN):b].strip()
    return '' if s == SUM_TODO else s


def _compose(title, summary, auto):
    return (f'{title}\n\n## 总结\n\n{SUM_BEGIN}\n{summary or SUM_TODO}\n{SUM_END}\n\n## 明细\n\n'
            f'{AUTO_BEGIN}\n{auto.rstrip()}\n{AUTO_END}\n')


def rollup_month(root, project, month):
    if not re.fullmatch(r'\d{4}-\d{2}', month or ''):
        raise JournalError('month must be YYYY-MM')
    days = [f for f in day_files(root, project) if f.name.startswith(month + '-')]
    if not days:
        raise JournalError(f'no day files for {month}')
    auto = ''.join(f'### {f.stem}\n' + '\n'.join(_key_points(f)) + '\n\n' for f in days)
    p = jdir(root, project) / 'months' / f'{month}.md'
    old = p.read_text(encoding='utf-8') if p.exists() else ''
    p.parent.mkdir(parents=True, exist_ok=True)
    handoff.atomic(p, _compose(f'# {month} 月度汇总 · `{project}`', _summary_of(old), auto).encode('utf-8'))
    return {'status': 'ROLLED_UP', 'file': str(p.relative_to(Path(root))), 'days': len(days),
            'summary_written': bool(_summary_of(old))}


def rollup_year(root, project, year):
    if not re.fullmatch(r'\d{4}', year or ''):
        raise JournalError('year must be YYYY')
    months = [f for f in _files(root, project, 'months', MONTH_RE) if f.name.startswith(year + '-')]
    if not months:
        raise JournalError(f'no month summaries for {year}; roll up the months first')
    auto = ''
    for f in months:
        s = _summary_of(f.read_text(encoding='utf-8'))
        auto += f'### {f.stem}\n' + (s if s else '（该月总结未写，见 months/' + f.name + '）') + '\n\n'
    p = jdir(root, project) / 'years' / f'{year}.md'
    old = p.read_text(encoding='utf-8') if p.exists() else ''
    p.parent.mkdir(parents=True, exist_ok=True)
    handoff.atomic(p, _compose(f'# {year} 年度汇总 · `{project}`', _summary_of(old), auto).encode('utf-8'))
    return {'status': 'ROLLED_UP', 'file': str(p.relative_to(Path(root))), 'months': len(months),
            'summary_written': bool(_summary_of(old))}


def check(root, project, tz=None):
    now = today(tz).date()
    cur_m, cur_y = now.strftime('%Y-%m'), now.strftime('%Y')
    months_with_days = sorted({f.name[:7] for f in day_files(root, project)})
    mfiles = {f.stem: f for f in _files(root, project, 'months', MONTH_RE)}
    yfiles = {f.stem: f for f in _files(root, project, 'years', YEAR_RE)}
    todo = []
    for m in months_with_days:
        f = mfiles.get(m)
        if not f:
            todo.append(f'{m}: 没有月度汇总（rollup --month {m}）' + ('' if m < cur_m else '；本月进行中，可先生成'))
        elif m < cur_m and not _summary_of(f.read_text(encoding='utf-8')):
            todo.append(f'{m}: 月度汇总的总结还没写（编辑 months/{m}.md 的总结段）')
    for y in sorted({m[:4] for m in mfiles}):
        f = yfiles.get(y)
        if not f:
            todo.append(f'{y}: 没有年度汇总（rollup --year {y}）')
        elif y < cur_y and not _summary_of(f.read_text(encoding='utf-8')):
            todo.append(f'{y}: 年度汇总的总结还没写（编辑 years/{y}.md 的总结段）')
    return todo


def recent(root, project, days=3, tz=None):
    """Reading order for a hand-over: year summary -> month summary -> newest N day files that exist."""
    j = jdir(root, project)
    out = []
    dfs = day_files(root, project)
    years = _files(root, project, 'years', YEAR_RE)
    months = _files(root, project, 'months', MONTH_RE)
    if not (dfs or years or months):
        return f'（`{project}` 还没有工作记录：journal.py add 开始写）\n'
    newest = dfs[-days:] if days > 0 else dfs
    want_months = {f.name[:7] for f in newest} | ({months[-1].stem} if months else set())
    want_years = {m[:4] for m in want_months} | ({years[-1].stem} if years else set())
    for f in years:
        if f.stem in want_years:
            out.append(f'===== 年度汇总 {f.relative_to(Path(root))}\n' + f.read_text(encoding='utf-8'))
    for f in months:
        if f.stem in want_months:
            out.append(f'===== 月度汇总 {f.relative_to(Path(root))}\n' + f.read_text(encoding='utf-8'))
    for f in newest:
        out.append(f'===== 日记录 {f.relative_to(Path(root))}\n' + f.read_text(encoding='utf-8'))
    older = len(dfs) - len(newest)
    if older > 0:
        out.append(f'（更早的 {older} 天日记录没有显示：已由月度/年度汇总概括；'
                   f'需要原文时看 {j.relative_to(Path(root))}/days/，slim 克隆用 git show HEAD:<路径> 读取）')
    todo = check(root, project, tz)
    if todo:
        out.append('待补的汇总：\n' + '\n'.join('- ' + t for t in todo))
    return '\n'.join(out) + '\n'


def brief_lines(root, project, days=3):
    """Short pointer block for handoff.py resume."""
    dfs = day_files(root, project)
    if not dfs:
        return []
    lines = ['', f'## 工作记录（按天写，按月/年汇总；共 {len(dfs)} 天本地可见）']
    for f in dfs[-days:]:
        kp = _key_points(f, fallback=2)
        lines.append(f'- {f.stem}：' + '；'.join(k.lstrip('- ').strip() for k in kp[:3])[:200])
    lines.append(f'- 读全文：python3 {Path(__file__).resolve()} recent --project {project}（年→月→最近 {days} 天）')
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=handoff.DEFAULT_ROOT)
    ap.add_argument('--tz')
    sub = ap.add_subparsers(dest='command', required=True)
    a1 = sub.add_parser('add'); a1.add_argument('--project', required=True); a1.add_argument('--text', required=True)
    a1.add_argument('--kind', default='done', choices=KINDS); a1.add_argument('--date'); a1.add_argument('--time')
    k = sub.add_parser('key'); k.add_argument('--project', required=True); k.add_argument('--text', required=True); k.add_argument('--date')
    r = sub.add_parser('recent'); r.add_argument('--project', required=True); r.add_argument('--days', type=int, default=3)
    ru = sub.add_parser('rollup'); ru.add_argument('--project', required=True)
    g = ru.add_mutually_exclusive_group(required=True); g.add_argument('--month'); g.add_argument('--year')
    c = sub.add_parser('check'); c.add_argument('--project', required=True)
    li = sub.add_parser('list'); li.add_argument('--project', required=True)
    a = ap.parse_args(argv)
    try:
        if a.command == 'add':
            res = add(a.root, a.project, a.text, a.kind, a.date, a.time, a.tz)
        elif a.command == 'key':
            res = key(a.root, a.project, a.text, a.date, a.tz)
        elif a.command == 'recent':
            sys.stdout.write(recent(a.root, a.project, a.days, a.tz)); return 0
        elif a.command == 'rollup':
            res = rollup_month(a.root, a.project, a.month) if a.month else rollup_year(a.root, a.project, a.year)
        elif a.command == 'check':
            todo = check(a.root, a.project, a.tz)
            print('\n'.join(todo) if todo else 'OK：已结束的月份和年份都有汇总'); return 0
        else:
            j = jdir(a.root, a.project)
            for f in sorted(j.rglob('*.md')) if j.is_dir() else []:
                print(f.relative_to(a.root))
            return 0
    except (JournalError, handoff.HandoffError) as e:
        print('ERROR ' + str(e), file=sys.stderr)
        return 2
    print(res)
    return 0


if __name__ == '__main__':
    sys.exit(main())
