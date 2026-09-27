#!/usr/bin/env python3
"""agent-skills: local-first skill registry (the user's own skills indexed + external skills cached,
reviewed and pinned). Standard library only (git CLI for fetch/sync).

Repository layout (root: --root, $AGENT_SKILLS_ROOT or /home/user/agent-skills):
  external/<name>/...          downloaded skill, unchanged
  external/<name>/SOURCE.json  provenance: source URL, repo, commit, path, trust tier, license,
                               static review verdict + findings, SHA256 of every file
  own/<name>.json              the user's own skills (they stay in their repositories)
  SOURCES.json                 trusted source tiers: official > curated > aggregator
  INDEX.json, INDEX.md         derived catalog (generated; never edit)

Lookup order when a task needs a skill:
  1. search locally (own + external);
  2. otherwise look in official/vendor repositories, then curated collections, then aggregators;
  3. fetch -> review (fail = do not use; warnings = read them first) -> add -> sync -> use.

Commands:
  search QUERY [-n N]
  fetch  (--repo OWNER/REPO | --url URL) [--path P] [--ref BRANCH]   shallow download to a temp dir
  review DIR
  add    --from DIR --name N --source-url URL [--repo R --commit C --path P] [--trust T] [--license L]
         [--allow-warn --note WHY] [--replace]
  register-own --from DIR --repo OWNER/REPO [--path P] [--commit C] [--visibility public|private]
         [--name N] [--description D] [--tags a,b]
  tag    --name N --tags a,b                  keywords (e.g. Chinese) for an external skill
  outdated [--name N]                        compare cached external skills with upstream
  verify | index
  privacy-scan --path DIR [--progress-root R] [--term X ...]   check a public repo for private names
  publish --dir REPO -m MESSAGE [--private] [--remote-url URL]  commit + push any skill repository:
                                             blocking privacy scan for public repos, secret checks, verify
  sync   -m MESSAGE [--token-file F]
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff  # noqa: E402

DEFAULT_ROOT = Path(os.environ.get('AGENT_SKILLS_ROOT') or '/home/user/agent-skills')
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
REPO_RE = re.compile(r'^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$')
TRUST = ('official', 'curated', 'aggregator')
ALLOWED_BINARY = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.ttf', '.otf', '.woff', '.woff2', '.pdf'}
EXEC_MAGIC = (b'\x7fELF', b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xfe\xed\xfa\xce')
MAX_TOTAL, MAX_FILE = 20 * 1024 * 1024, 10 * 1024 * 1024
DERIVED = ('INDEX.json', 'INDEX.md')
SYNC_PATHS = ('external', 'own', 'SOURCES.json', 'INDEX.json', 'INDEX.md', 'README.md', 'LOCATION.json', '.gitignore')
FAIL_RULES = [
    ('pipe-to-shell', re.compile(r'\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b', re.I)),
    ('pipe-to-interpreter', re.compile(r'\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(python3?|node|perl|ruby)\b', re.I)),
    ('decode-and-execute', re.compile(r'base64\s+(-d|--decode)\b[^\n]*\|\s*(ba)?sh|\bexec\s*\(\s*(base64|codecs|zlib)\.|'
                                      r'\beval\s*\(\s*(atob|Buffer\.from)\s*\(', re.I)),
    ('destructive-command', re.compile(r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\$HOME)(\s|$|/\s|/$|/\*)|\bmkfs(\.\w+)?\s|'
                                       r'\bdd\s+if=[^\n]*of=/dev/(sd|nvme|disk|hd)|:\(\)\s*\{\s*:\|:&\s*\};:', re.I)),
    ('credential-access', re.compile(r'~/\.ssh\b|/\.ssh/|\bid_(rsa|ed25519|ecdsa)\b|\.aws/credentials|\.git-credentials|'
                                     r'/etc/shadow|security\s+find-(generic|internet)-password', re.I)),
    ('reverse-shell', re.compile(r'/dev/tcp/\S+|\bnc\s+(-\w*e|-\w*c)\s|\bsocat\b[^\n]*\bexec:', re.I)),
]
WARN_RULES = [
    ('network', re.compile(r'\b(requests\.(get|post|put|patch|delete)|urllib\.request|http\.client|httpx\.|aiohttp|'
                           r'fetch\s*\(|XMLHttpRequest|socket\.socket)|\b(curl|wget)\s', re.I)),
    ('process-execution', re.compile(r'\b(subprocess\.|os\.system\s*\(|os\.popen\s*\(|child_process|execSync|spawnSync)', re.I)),
    ('dynamic-code', re.compile(r'(?<![\w.])(eval|exec)\s*\(')),
    ('package-install', re.compile(r'\b(pip3?\s+install|npm\s+(i|install)\b|apt(-get)?\s+install|brew\s+install)', re.I)),
    ('environment-secrets', re.compile(r'\b(GH_TOKEN|GITHUB_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|AWS_SECRET_ACCESS_KEY)\b')),
    ('credential-mention', re.compile(r'\.netrc\b|\bkeychain\b', re.I)),
    ('long-encoded-blob', re.compile(r'[A-Za-z0-9+/=]{2000,}')),
]


class SkillError(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def dump(x):
    return (json.dumps(x, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def frontmatter(skill_md):
    """Minimal SKILL.md frontmatter reader (key: value lines between --- markers)."""
    try:
        text = Path(skill_md).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 3)
    out = {}
    for line in text[3:end if end > 0 else 0].splitlines():
        m = re.match(r'^([A-Za-z_][\w-]*)\s*:\s*(.*)$', line)
        if m and m.group(2).strip() not in ('', '|', '>'):
            out[m.group(1)] = m.group(2).strip().strip('"\'')
    return out


def detect_license(skill_dir, fm=None):
    for f in sorted(Path(skill_dir).glob('*')):
        if f.is_file() and re.match(r'^(LICEN[SC]E|COPYING)', f.name, re.I):
            head = f.read_text(encoding='utf-8', errors='ignore')[:3000]
            for pattern, name in (('MIT License', 'MIT'), ('Apache License', 'Apache-2.0'), ('BSD', 'BSD'),
                                  ('GNU GENERAL PUBLIC', 'GPL'), ('Mozilla Public License', 'MPL-2.0'),
                                  ('Creative Commons', 'CC'), ('Permission is hereby granted', 'MIT')):
                if pattern.lower() in head.lower():
                    return name
            return f'custom (see {f.name})'
    lic = (fm or {}).get('license')
    return f'declared: {lic}' if lic else 'UNKNOWN'


def _text(data, suffix):
    if b'\0' in data[:8192]:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None if suffix in ALLOWED_BINARY or data[:4] in EXEC_MAGIC else data.decode('latin-1')


def review(skill_dir):
    """Static security review. fail: do not use. warn: read the findings before using."""
    d = Path(skill_dir)
    findings, hosts, total, files = [], set(), 0, 0

    def add(level, rule, rel, line=None, excerpt=''):
        if len(findings) < 300:
            findings.append({'level': level, 'rule': rule, 'file': rel, 'line': line, 'excerpt': excerpt[:120]})

    fm = frontmatter(d / 'SKILL.md')
    if not (d / 'SKILL.md').is_file():
        add('fail', 'not-a-skill', 'SKILL.md', excerpt='SKILL.md missing')
    elif not (fm.get('name') and fm.get('description')):
        add('fail', 'not-a-skill', 'SKILL.md', excerpt='frontmatter needs name and description')
    for f in sorted(d.rglob('*')):
        rel = f.relative_to(d).as_posix()
        if '.git' in f.relative_to(d).parts:
            continue
        if f.is_symlink():
            add('fail', 'symlink', rel, excerpt='symlinks can point outside the skill')
            continue
        if not f.is_file():
            continue
        files += 1
        size = f.stat().st_size
        total += size
        if size > MAX_FILE:
            add('fail', 'file-too-large', rel, excerpt=f'{size} bytes')
            continue
        data = f.read_bytes()
        text = _text(data, f.suffix.lower())
        if text is None:
            if data[:4] in EXEC_MAGIC or data[:2] == b'MZ':
                add('fail', 'executable-binary', rel)
            elif f.suffix.lower() not in ALLOWED_BINARY:
                add('fail', 'unreviewable-binary', rel, excerpt=f.suffix or '(no suffix)')
            continue
        hosts.update(h.lower() for h in re.findall(r'https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})', text))
        for rule, rx in FAIL_RULES:
            for m in rx.finditer(text):
                add('fail', rule, rel, text.count('\n', 0, m.start()) + 1, m.group(0))
        for rule, rx in WARN_RULES:
            m = rx.search(text)
            if m:
                add('warn', rule, rel, text.count('\n', 0, m.start()) + 1, m.group(0))
        if handoff.SECRET_RE.search(text):
            add('fail', 'embedded-credential', rel, excerpt='(value redacted)')
    if total > MAX_TOTAL:
        add('fail', 'skill-too-large', '.', excerpt=f'{total} bytes')
    levels = {x['level'] for x in findings}
    return {'verdict': 'fail' if 'fail' in levels else 'warn' if 'warn' in levels else 'pass',
            'name': fm.get('name'), 'description': fm.get('description'), 'license': detect_license(d, fm),
            'files': files, 'bytes': total, 'hosts': sorted(hosts)[:40], 'findings': findings}


def file_hashes(folder, skip=('SOURCE.json',)):
    folder = Path(folder)
    return {f.relative_to(folder).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(folder.rglob('*')) if f.is_file() and '.git' not in f.relative_to(folder).parts
            and f.relative_to(folder).as_posix() not in skip}


def load_sources(root):
    try:
        return json.loads((Path(root) / 'SOURCES.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def trust_for(root, repo):
    src = load_sources(root)
    if repo:
        for tier in ('official', 'curated'):
            if any(repo.lower() == str(x).lower() for x in src.get(tier, [])):
                return tier
    return 'aggregator'


def add(root, src_dir, name, source_url, repo=None, commit=None, path=None, trust=None, license_=None,
        allow_warn=False, replace=False, note=None, tags=(), clone_url=None):
    root, src = Path(root), Path(src_dir)
    if not NAME_RE.fullmatch(name or ''):
        raise SkillError('name must be a lowercase slug')
    if not str(source_url).startswith('https://'):
        raise SkillError('source-url must be an https URL (where the skill came from)')
    if repo and not REPO_RE.fullmatch(repo):
        raise SkillError('repo must look like owner/name')
    earned = trust_for(root, repo)
    trust = trust or earned
    if trust not in TRUST or TRUST.index(trust) < TRUST.index(earned):
        raise SkillError(f'trust {trust!r} not allowed for {repo or source_url}: SOURCES.json grants {earned!r}')
    result = review(src)
    fails = [f for f in result['findings'] if f['level'] == 'fail']
    if fails:
        raise SkillError('security review failed; do not use this skill: ' +
                         '; '.join(f"{f['rule']} in {f['file']}:{f['line'] or ''}" for f in fails[:8]))
    if result['verdict'] == 'warn' and not allow_warn:
        raise SkillError('security review has warnings; read them (skills.py review DIR) and re-run with --allow-warn '
                         'only if each one is expected for this skill')
    dest = root / 'external' / name
    if dest.exists():
        if not replace:
            raise SkillError(f'external/{name} exists; use --replace to update it (git keeps the old version)')
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns('.git'))
    for f in dest.rglob('*'):  # workspace snapshots drop executable bits; store plain files, run via python3/bash
        if f.is_file():
            os.chmod(f, 0o644)
    source = {'name': name, 'source_url': source_url, 'repo': repo, 'commit': commit, 'path': path, 'trust': trust,
              'license': license_ or result['license'], 'fetched_at': now(), 'modes_normalized': True,
              'tags': [t for t in tags if t], **({'clone_url': clone_url} if clone_url else {}),
              'description': result['description'] or '',
              'review': {'verdict': result['verdict'], 'reviewer': 'skills.py static review v1', 'reviewed_at': now(),
                         'hosts': result['hosts'], 'findings': result['findings'][:60],
                         **({'notes': str(note)[:2000]} if note else {})},
              'files': file_hashes(dest)}
    handoff.atomic(dest / 'SOURCE.json', dump(source))
    index(root)
    return {'added': name, 'trust': trust, 'license': source['license'], 'verdict': result['verdict'],
            'files': len(source['files'])}


def register_own(root, src_dir, repo, path='', commit=None, visibility='public', name=None, description=None, tags=()):
    fm = frontmatter(Path(src_dir) / 'SKILL.md')
    name = name or fm.get('name')
    description = description or fm.get('description')
    if not name or not NAME_RE.fullmatch(name):
        raise SkillError('own skill needs a slug name (frontmatter name or --name)')
    if not description:
        raise SkillError('own skill needs a description (frontmatter description or --description)')
    if not REPO_RE.fullmatch(repo or ''):
        raise SkillError('repo must look like owner/name')
    if not commit and (Path(src_dir) / '.git').exists():
        import subprocess
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(src_dir), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True).stdout.strip() or None
    entry = {'name': name, 'description': description, 'repo': repo, 'path': path or '', 'commit': commit,
             'visibility': visibility, 'tags': list(tags), 'registered_at': now()}
    try:
        handoff.check_secret(entry, name)
    except handoff.HandoffError as e:
        raise SkillError(str(e))
    handoff.atomic(Path(root) / 'own' / f'{name}.json', dump(entry))
    index(root)
    return {'registered': name, 'repo': repo, 'commit': commit}


def catalog(root):
    root = Path(root)
    rows = []
    for f in sorted((root / 'own').glob('*.json')) if (root / 'own').exists() else []:
        e = json.loads(f.read_text(encoding='utf-8'))
        rows.append({'name': e['name'], 'kind': 'own', 'description': e['description'], 'tags': e.get('tags', []),
                     'location': f"https://github.com/{e['repo']}" + (f"/tree/main/{e['path']}" if e.get('path') else ''),
                     'commit': e.get('commit'), 'visibility': e.get('visibility')})
    for f in sorted((root / 'external').glob('*/SOURCE.json')) if (root / 'external').exists() else []:
        e = json.loads(f.read_text(encoding='utf-8'))
        rows.append({'name': e['name'], 'kind': 'external', 'description': e.get('description', ''), 'tags': e.get('tags', []),
                     'location': f'external/{e["name"]}', 'source_url': e['source_url'], 'commit': e.get('commit'),
                     'trust': e['trust'], 'license': e['license'], 'verdict': e['review']['verdict']})
    return sorted(rows, key=lambda r: (r['kind'] != 'own', r['name']))


def index(root):
    root = Path(root)
    rows = catalog(root)
    handoff.atomic(root / 'INDEX.json', dump({'schema': 'agent-skills-index/1', 'skills': rows}))
    lines = ['# Skill 总目录（派生视图，skills.py 生成，勿手改）', '',
             '查找顺序：先在这里找（自己的 + 已缓存的外部 skill）→ 找不到再按 SOURCES.json 的来源顺序外部下载。', '',
             '| 名称 | 类型 | 说明 | 位置 | 来源等级/许可/检查 |', '|---|---|---|---|---|']
    for r in rows:
        extra = '自己的' if r['kind'] == 'own' else f"{r['trust']} · {r['license']} · {r['verdict']}"
        desc = re.sub(r'\s+', ' ', r['description'])[:120].replace('|', '/')
        lines.append(f"| {r['name']} | {'自有' if r['kind'] == 'own' else '外部'} | {desc} | {r['location']} | {extra} |")
    handoff.atomic(root / 'INDEX.md', ('\n'.join(lines) + '\n').encode())
    return {'skills': len(rows)}


def search(root, query, n=10):
    terms = [t.lower() for t in query.split() if t.strip()]
    if not terms:
        raise SkillError('Empty query')
    out = []
    for r in catalog(root):
        name, desc, tags = r['name'].lower(), r['description'].lower(), ' '.join(r['tags']).lower()
        score = 0
        for t in terms:
            hit = (5 if t in name else 0) + (3 if t in tags else 0) + (2 if t in desc else 0)
            if not hit:
                score = 0
                break
            score += hit
        if score:
            out.append(dict(r, score=score + (1 if r['kind'] == 'own' else 0)))
    return sorted(out, key=lambda r: -r['score'])[:n]


def verify(root):
    problems, ok = [], []
    for f in sorted((Path(root) / 'external').glob('*/SOURCE.json')) if (Path(root) / 'external').exists() else []:
        e = json.loads(f.read_text(encoding='utf-8'))
        actual = file_hashes(f.parent)
        if actual != e.get('files'):
            changed = sorted(k for k in set(actual) | set(e.get('files', {})) if actual.get(k) != e['files'].get(k))
            problems.append({'skill': e['name'], 'changed_files': changed[:20]})
        else:
            ok.append(e['name'])
    return {'pass': not problems, 'verified': ok, 'problems': problems}


def fetch(repo=None, url=None, path=None, ref=None, token=None):
    import progress_sync as ps
    if repo and not REPO_RE.fullmatch(repo):
        raise SkillError('repo must look like owner/name')
    url = url or f'https://github.com/{repo}.git'
    tmp = Path(tempfile.mkdtemp(prefix='skill-fetch-'))
    git = ps.Git(token)
    try:
        git.run(['clone', '--quiet', '--depth', '1', '--filter=blob:none', '--sparse'] + (['--branch', ref] if ref else [])
                + [url, str(tmp / 'repo')], tmp)
        if path:
            handoff.safe_relative(path)
            git.run(['sparse-checkout', 'set', path], tmp / 'repo')
        commit = git.out(['rev-parse', 'HEAD'], tmp / 'repo')
    finally:
        git.close()
    skill_dir = tmp / 'repo' / path if path else tmp / 'repo'
    if not skill_dir.is_dir():
        raise SkillError(f'path not found in repository: {path}')
    web = (f'https://github.com/{repo}/tree/{commit}/{path}' if path else f'https://github.com/{repo}/tree/{commit}') if repo else url
    return {'dir': str(skill_dir), 'commit': commit, 'repo': repo, 'path': path, 'source_url': web,
            'suggested_trust': None}


def privacy_scan(path, progress_root=None, terms=()):
    words = list(terms)
    if progress_root and (Path(progress_root) / 'PRIVACY_TERMS.json').exists():
        words += json.loads((Path(progress_root) / 'PRIVACY_TERMS.json').read_text(encoding='utf-8')).get('terms', [])
    words = [w for w in dict.fromkeys(str(x) for x in words) if len(w) >= 2]
    rx = re.compile('|'.join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.I) if words else None
    hits = []
    path = Path(path)
    if (path / '.git').exists():  # scan exactly what would be published: tracked + untracked, not ignored
        import subprocess
        listed = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard', '-z'], cwd=str(path),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode('utf-8', 'replace')
        candidates = [path / x for x in listed.split('\0') if x]
    else:
        candidates = [f for f in sorted(path.rglob('*')) if not {'.git', '__pycache__', 'node_modules', '.venv'} & set(f.relative_to(path).parts)]
    for f in candidates:
        if not f.is_file() or f.stat().st_size > MAX_FILE:
            continue
        text = f.read_bytes().decode('utf-8', 'ignore')
        rel = f.relative_to(path).as_posix()
        for m in (rx.finditer(text) if rx else []):
            hits.append({'file': rel, 'line': text.count('\n', 0, m.start()) + 1, 'term': m.group(0)})
        if handoff.SECRET_RE.search(text):
            hits.append({'file': rel, 'line': None, 'term': '(credential pattern, value redacted)'})
    return {'clean': not hits, 'terms_checked': len(words), 'hits': hits[:200]}


def tag(root, name, tags):
    f = Path(root) / 'external' / name / 'SOURCE.json'
    if not f.exists():
        raise SkillError(f'no external skill named {name}')
    e = json.loads(f.read_text(encoding='utf-8'))
    e['tags'] = list(dict.fromkeys(list(e.get('tags', [])) + [t for t in tags if t]))
    handoff.atomic(f, dump(e))
    index(root)
    return {'name': name, 'tags': e['tags']}


def outdated(root, name=None, token=None):
    """Compare cached external skills with the current upstream version of the same path."""
    out = []
    for f in sorted((Path(root) / 'external').glob('*/SOURCE.json')) if (Path(root) / 'external').exists() else []:
        e = json.loads(f.read_text(encoding='utf-8'))
        if name and e['name'] != name:
            continue
        if not (e.get('repo') or e.get('clone_url')):
            out.append({'name': e['name'], 'status': 'unknown', 'reason': 'no repository recorded'})
            continue
        try:
            latest = fetch(repo=e.get('repo') if not e.get('clone_url') else None, url=e.get('clone_url'),
                           path=e.get('path'), token=token)
        except Exception as err:  # noqa: BLE001 - report per skill, keep going
            out.append({'name': e['name'], 'status': 'upstream_unreachable', 'reason': str(err)[:200]})
            continue
        now_files, old = file_hashes(latest['dir']), e.get('files', {})
        changed = sorted(k for k in set(now_files) | set(old) if now_files.get(k) != old.get(k))
        out.append({'name': e['name'], 'pinned_commit': e.get('commit'), 'upstream_commit': latest['commit'],
                    'status': 'changed' if changed else 'up_to_date', 'changed_files': changed[:30],
                    'update': (f"skills.py review {latest['dir']} && skills.py add --from {latest['dir']} --name {e['name']} "
                               f"--source-url {latest['source_url']} --repo {e.get('repo') or ''} --commit {latest['commit']} "
                               f"--path {e.get('path') or ''} --replace") if changed else None})
    return out


def publish(repo_dir, message, token=None, progress_root=None, private=None, remote_url=None):
    """Commit + push a skill repository (e.g. when a lesson becomes a rule). Public repositories must
    pass the privacy scan first; every repository gets the secret/size checks and ls-remote verification."""
    import progress_sync as ps
    repo_dir = Path(repo_dir).resolve()
    if not (repo_dir / '.git').exists():
        raise SkillError(f'not a git repository: {repo_dir}')
    try:
        loc = json.loads((repo_dir / 'LOCATION.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        loc = {}
    if private is None:
        private = str(loc.get('visibility', '')).lower() == 'private'
    scan = None
    if not private:
        scan = privacy_scan(repo_dir, progress_root)
        if not scan['clean']:
            raise SkillError('privacy scan blocked publishing to a public repository: ' +
                             '; '.join(f"{h['term']} in {h['file']}:{h['line'] or ''}" for h in scan['hits'][:8]))
    git = ps.Git(token)
    try:
        result = ps.push_paths(repo_dir, git, ['.'], message, remote_url=remote_url)
    finally:
        git.close()
    result['privacy_scan'] = 'skipped (private repository)' if private else f"clean ({scan['terms_checked']} terms)"
    return result


def sync(root, message, token=None):
    import progress_sync as ps
    git = ps.Git(token)
    try:
        return ps.push_paths(root, git, list(SYNC_PATHS), message, regenerate=index, derived=DERIVED)
    finally:
        git.close()


def _token(token_file):
    if token_file:
        return Path(token_file).read_text(encoding='utf-8').strip() or None
    return os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    sub = ap.add_subparsers(dest='command', required=True)
    s = sub.add_parser('search')
    s.add_argument('query')
    s.add_argument('-n', type=int, default=10)
    f = sub.add_parser('fetch')
    g = f.add_mutually_exclusive_group(required=True)
    g.add_argument('--repo')
    g.add_argument('--url')
    f.add_argument('--path')
    f.add_argument('--ref')
    f.add_argument('--token-file', type=Path)
    r = sub.add_parser('review')
    r.add_argument('dir', type=Path)
    a_ = sub.add_parser('add')
    a_.add_argument('--from', dest='src', type=Path, required=True)
    a_.add_argument('--name', required=True)
    a_.add_argument('--source-url', required=True)
    a_.add_argument('--repo')
    a_.add_argument('--commit')
    a_.add_argument('--path')
    a_.add_argument('--trust', choices=TRUST)
    a_.add_argument('--license')
    a_.add_argument('--allow-warn', action='store_true')
    a_.add_argument('--replace', action='store_true')
    a_.add_argument('--note', help='why the warnings are acceptable (stored in SOURCE.json)')
    a_.add_argument('--tags', default='', help='comma separated keywords, e.g. Chinese terms')
    a_.add_argument('--clone-url', help='non-GitHub clone URL used by outdated')
    tg = sub.add_parser('tag')
    tg.add_argument('--name', required=True)
    tg.add_argument('--tags', required=True)
    od = sub.add_parser('outdated')
    od.add_argument('--name')
    od.add_argument('--token-file', type=Path)
    pb = sub.add_parser('publish')
    pb.add_argument('--dir', type=Path, required=True)
    pb.add_argument('-m', '--message', required=True)
    pb.add_argument('--private', action='store_true', help='skip the privacy scan (private repository)')
    pb.add_argument('--remote-url')
    pb.add_argument('--progress-root', type=Path, default=Path(os.environ.get('AGENT_PROGRESS_ROOT') or '/home/user/agent-progress'))
    pb.add_argument('--token-file', type=Path)
    o = sub.add_parser('register-own')
    o.add_argument('--from', dest='src', type=Path, required=True)
    o.add_argument('--repo', required=True)
    o.add_argument('--path', default='')
    o.add_argument('--commit')
    o.add_argument('--visibility', choices=('public', 'private'), default='public')
    o.add_argument('--name')
    o.add_argument('--description')
    o.add_argument('--tags', default='')
    sub.add_parser('verify')
    sub.add_parser('index')
    p = sub.add_parser('privacy-scan')
    p.add_argument('--path', type=Path, required=True)
    p.add_argument('--progress-root', type=Path, default=Path(os.environ.get('AGENT_PROGRESS_ROOT') or '/home/user/agent-progress'))
    p.add_argument('--term', action='append', default=[])
    y = sub.add_parser('sync')
    y.add_argument('-m', '--message', required=True)
    y.add_argument('--token-file', type=Path)
    a = ap.parse_args(argv)
    try:
        if a.command == 'search':
            result = search(a.root, a.query, a.n)
        elif a.command == 'fetch':
            result = fetch(a.repo, a.url, a.path, a.ref, _token(a.token_file))
            result['suggested_trust'] = trust_for(a.root, a.repo)
        elif a.command == 'review':
            result = review(a.dir)
        elif a.command == 'add':
            result = add(a.root, a.src, a.name, a.source_url, a.repo, a.commit, a.path, a.trust, a.license,
                         a.allow_warn, a.replace, a.note, [x.strip() for x in a.tags.split(',') if x.strip()], a.clone_url)
        elif a.command == 'tag':
            result = tag(a.root, a.name, [x.strip() for x in a.tags.split(',') if x.strip()])
        elif a.command == 'outdated':
            result = outdated(a.root, a.name, _token(a.token_file))
        elif a.command == 'publish':
            result = publish(a.dir, a.message, _token(a.token_file), a.progress_root, True if a.private else None, a.remote_url)
        elif a.command == 'register-own':
            result = register_own(a.root, a.src, a.repo, a.path, a.commit, a.visibility, a.name, a.description,
                                  [x.strip() for x in a.tags.split(',') if x.strip()])
        elif a.command == 'verify':
            result = verify(a.root)
        elif a.command == 'index':
            result = index(a.root)
        elif a.command == 'privacy-scan':
            result = privacy_scan(a.path, a.progress_root, a.term)
        else:
            result = sync(a.root, a.message, _token(a.token_file))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if a.command in ('verify', 'privacy-scan'):
            return 0 if result.get('pass', result.get('clean')) else 1
        if a.command == 'review':
            return 0 if result['verdict'] != 'fail' else 1
        return 0
    except (SkillError, handoff.HandoffError, OSError, ValueError, KeyError) as e:
        print('Skills failed: ' + handoff.SECRET_RE.sub('***', str(e))[:600], file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - includes SyncError; never leak data
        print('Skills failed: ' + type(e).__name__ + ': ' + handoff.SECRET_RE.sub('***', str(e))[:400], file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
