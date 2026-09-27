#!/usr/bin/env python3
"""Git sync for the private agent-progress repository (v2, standard library only).

  clone  --repo OWNER/NAME [--dest DIR] [--depth N]   (re-runnable: an existing clone is repaired and pulled)
  doctor                                               one-shot health check with concrete fixes
  pull                                                 fast-forward / rebase onto the remote safely
  status [--fetch] [--project D/P ...]
  push   --project D/P [--project ...] [--path REL ...] -m MESSAGE [--dry-run]
  save   --project D/P --expected ID --patch FILE|- --note TEXT [-m MESSAGE] [--path REL ...] [--dry-run]
                                                       handoff update + push in one step (end-of-turn)

Workspace snapshots on some platforms drop .git/config (remote + identity). Every command re-adds a
missing `origin` from LOCATION.json (progress_remote_url) or --remote-url, and commits fall back to
the last commit's author when user.email is unset.

Credentials: $GH_TOKEN or $GITHUB_TOKEN, or --token-file PATH (outside the repository).
The token reaches git only through a throw-away GIT_ASKPASS helper that reads it from the
environment of the git child process. It is never written to .git/config, remote URLs,
commits, receipts or logs, and stored credential helpers are disabled for these calls.

Verification: after a normal (never forced) push, `git ls-remote` must return exactly the
local commit id. A Git commit id is a hash over the complete tree, so equal ids prove the
remote holds byte-identical files. No receipt commit and no fresh clone are needed; the
sync state of any checkpoint can be derived later with `status --fetch`.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff  # noqa: E402

FORBIDDEN = re.compile(r'(^|/)(\.env[^/]*|\.netrc|\.git-credentials|credentials|\.credentials)(/|$)'
                       r'|\.enc\.json$|\.pem$|\.key$|(^|/)\.write\.lock$|\.tmp$')
DERIVED = re.compile(r'^(INDEX\.md|projects/[a-z0-9-]+/[a-z0-9-]+/CURRENT\.md)$')
REPO_RE = re.compile(r'^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$')
USERINFO = re.compile(r'(https?://)[^/@\s]+@')
FALLBACK_IDENTITY = ['-c', 'user.name=agent-progress', '-c', 'user.email=agent-progress@users.noreply.github.com']


class SyncError(Exception):
    pass


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


class Git:
    """Runs git with an in-memory token (if any) and without stored credential helpers."""

    def __init__(self, token=None):
        self.token = token or None
        self.tmp = None
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('GIT_TRACE') and k not in {'GIT_CURL_VERBOSE', 'GH_TOKEN', 'GITHUB_TOKEN',
                                                              'GIT_ASKPASS', 'SSH_ASKPASS'}}
        env.update(GIT_TERMINAL_PROMPT='0', GIT_EDITOR='true', GIT_MERGE_AUTOEDIT='no')
        if self.token:
            self.tmp = tempfile.mkdtemp(prefix='progress-sync-')
            helper = Path(self.tmp) / 'askpass.sh'
            helper.write_text('#!/bin/sh\ncase "$1" in\n  *[Uu]sername*) echo x-access-token ;;\n'
                              '  *) printf \'%s\\n\' "$PROGRESS_SYNC_TOKEN" ;;\nesac\n')
            helper.chmod(0o700)
            env['GIT_ASKPASS'] = str(helper)
            env['PROGRESS_SYNC_TOKEN'] = self.token
        self.env = env

    def close(self):
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def redact(self, text):
        if self.token:
            text = text.replace(self.token, '***')
        text = handoff.SECRET_RE.sub('***', text)
        return USERINFO.sub(r'\1***@', text)

    def run(self, args, cwd, check=True):
        cmd = ['git', '-c', 'credential.helper=', '-c', 'core.quotepath=off', *args]
        p = subprocess.run(cmd, cwd=str(cwd), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=1800)
        if check and p.returncode:
            detail = self.redact(p.stderr.decode('utf-8', 'replace')).strip()[-600:]
            raise SyncError(f'git {args[0]} failed (exit {p.returncode}): {detail}')
        return p

    def out(self, args, cwd, check=True):
        return self.run(args, cwd, check).stdout.decode('utf-8', 'replace').strip()


def read_token(token_file, root):
    if token_file:
        path = Path(token_file).resolve()
        if path.is_relative_to(Path(root).resolve()):
            raise SyncError('--token-file must live outside the progress repository')
        return path.read_text(encoding='utf-8').strip() or None
    return os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN') or None


# ---------------------------------------------------------------- repository helpers

def location_remote(root):
    try:
        url = json.loads((Path(root) / 'LOCATION.json').read_text(encoding='utf-8')).get('progress_remote_url')
    except (OSError, ValueError, AttributeError):
        return None
    return url if isinstance(url, str) and url.startswith(('https://', 'git@', '/')) and not USERINFO.search(url) else None


def ensure_origin(root, git, remote_url=None):
    """Re-add origin if a workspace snapshot dropped .git/config. Returns the repairs made."""
    if git.out(['remote', 'get-url', 'origin'], root, check=False):
        return []
    url = remote_url or location_remote(root)
    if not url:
        raise SyncError("git remote 'origin' is missing (workspace snapshots can drop .git/config) and LOCATION.json "
                        'has no progress_remote_url; pass --remote-url https://github.com/OWNER/agent-progress.git')
    if USERINFO.search(url):
        raise SyncError('Refusing a remote URL with embedded credentials; use a clean URL plus GH_TOKEN')
    git.run(['remote', 'add', 'origin', url], root)
    return ['origin']


def repo_info(root, git, remote_url=None):
    root = Path(root)
    if not (root / '.git').exists():
        raise SyncError('Not a git repository: ' + str(root))
    branch = git.out(['symbolic-ref', '--quiet', '--short', 'HEAD'], root, check=False)
    if not branch:
        raise SyncError('Detached HEAD: check out the default branch first')
    repairs = ensure_origin(root, git, remote_url)
    url = git.out(['remote', 'get-url', 'origin'], root, check=False)
    return {'branch': branch, 'origin_url_has_credentials': bool(USERINFO.search(url)), 'repairs': repairs}


def ensure_no_operation_in_progress(root):
    g = Path(root) / '.git'
    for marker in ('rebase-merge', 'rebase-apply', 'MERGE_HEAD', 'CHERRY_PICK_HEAD'):
        if (g / marker).exists():
            raise SyncError(f'A git operation is in progress ({marker}); finish or abort it first')


def status_entries(root, git):
    data = git.run(['status', '--porcelain=v1', '-z', '--untracked-files=all'], root).stdout.decode('utf-8', 'replace')
    parts, entries, i = data.split('\0'), [], 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        xy, path = entry[:2], entry[3:]
        entries.append((xy, path))
        if xy[0] in 'RC':
            i += 1  # skip the original path of a rename/copy
    return entries


def under(path, prefixes):
    return any(path == p or path.startswith(p.rstrip('/') + '/') for p in prefixes)


def show_bytes(git, root, spec):
    p = git.run(['cat-file', 'blob', spec], root, check=False)
    return p.stdout if p.returncode == 0 else None


def identity(git, root):
    """(-c args, source). Prefer git config; else the last commit's author (snapshots may drop .git/config)."""
    if git.out(['config', 'user.email'], root, check=False):
        return [], 'git config'
    last = git.out(['log', '-1', '--format=%an%x00%ae'], root, check=False)
    if '\0' in last:
        name, mail = last.split('\0', 1)
        if name.strip() and mail.strip():
            return ['-c', f'user.name={name.strip()}', '-c', f'user.email={mail.strip()}'], 'last commit author'
    return FALLBACK_IDENTITY, 'fallback noreply identity'


def identity_args(git, root):  # backward-compatible helper
    return identity(git, root)[0]


def remote_branch_exists(git, root, branch):
    return bool(git.out(['ls-remote', '--heads', 'origin', branch], root))


def refresh_views(root, projects):
    """Regenerate derived views for the selected projects and the index only."""
    root = Path(root)
    changed = []
    for proj in projects:
        pdir = handoff.project_dir(root, proj)
        _, cp = handoff.checked_head(pdir)
        data = handoff.current_bytes(cp)
        if not (pdir / 'CURRENT.md').exists() or (pdir / 'CURRENT.md').read_bytes() != data:
            handoff.atomic(pdir / 'CURRENT.md', data)
            changed.append(f'projects/{proj}/CURRENT.md')
    data = handoff.index_bytes(root)
    if not (root / 'INDEX.md').exists() or (root / 'INDEX.md').read_bytes() != data:
        handoff.atomic(root / 'INDEX.md', data)
        changed.append('INDEX.md')
    return changed


def scan_content(path, blob, git, max_bytes):
    problems = []
    if FORBIDDEN.search(path):
        problems.append(f'credential-like or temporary file: {path}')
    if blob is not None:
        if len(blob) > max_bytes:
            problems.append(f'{path} exceeds {max_bytes // (1024 * 1024)} MB (keep media in asset repositories)')
        text = blob.decode('utf-8', 'ignore')
        if handoff.SECRET_RE.search(text) or (git.token and git.token in text):
            problems.append(f'potential credential in {path} (value redacted)')
    return problems


# ---------------------------------------------------------------- commands

def clone(git, dest, repo=None, url=None, depth=None):
    if repo:
        if not REPO_RE.fullmatch(repo):
            raise SyncError('--repo must look like owner/name')
        url = f'https://github.com/{repo}.git'
    if not url or USERINFO.search(url):
        raise SyncError('A clean remote URL without embedded credentials is required')
    dest = Path(dest)
    if (dest / '.git').exists():  # already cloned (possibly restored from a snapshot): repair + pull
        return dict(pull(dest, git, url), dest=str(dest), note='existing clone reused')
    if dest.exists() and any(dest.iterdir()):
        raise SyncError('Destination exists and is not empty: ' + str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ['clone', '--quiet'] + (['--depth', str(depth)] if depth else []) + [url, str(dest)]
    git.run(args, dest.parent)
    return {'status': 'CLONED', 'dest': str(dest),
            'commit': git.out(['rev-parse', '--verify', '--quiet', 'HEAD'], dest, check=False) or None,
            'branch': git.out(['symbolic-ref', '--quiet', '--short', 'HEAD'], dest, check=False)}


def status(root, git, fetch=False, projects=None, remote_url=None):
    root = Path(root)
    info = repo_info(root, git, remote_url)
    branch = info['branch']
    result = {'branch': branch, 'fetched_now': False, 'checked_at': utcnow()}
    if info['repairs']:
        result['repairs'] = info['repairs']
    if fetch:
        try:
            if remote_branch_exists(git, root, branch):
                git.run(['fetch', '--quiet', 'origin', branch], root)
            result['fetched_now'] = True
        except SyncError as e:
            result['fetch_error'] = str(e)
    remote_ref = f'refs/remotes/origin/{branch}'
    has_remote = git.run(['rev-parse', '--verify', '--quiet', remote_ref], root, check=False).returncode == 0
    result['local_commit'] = git.out(['rev-parse', '--verify', '--quiet', 'HEAD'], root, check=False)
    if has_remote:
        behind, ahead = git.out(['rev-list', '--left-right', '--count', f'{remote_ref}...HEAD'], root).split()
        result.update(remote_commit=git.out(['rev-parse', remote_ref], root), ahead=int(ahead), behind=int(behind))
    dirty = [path for _, path in status_entries(root, git)]
    result['uncommitted_paths'] = len(dirty)
    result['uncommitted_sample'] = dirty[:20]
    if info['origin_url_has_credentials']:
        result['warning'] = 'origin URL embeds credentials (redacted); use a clean URL plus GH_TOKEN instead'
    selected = projects or [handoff.project_id(p) for p in handoff.all_projects(root)]
    rows = []
    for proj in selected:
        pdir = handoff.project_dir(root, proj)
        rel = f'projects/{proj}'
        if not (pdir / 'HEAD.json').exists():
            rows.append({'project': proj, 'sync_state': 'NOT_IN_WORKSPACE'})
            continue
        head_bytes = (pdir / 'HEAD.json').read_bytes()
        head = json.loads(head_bytes)
        row = {'project': proj, 'checkpoint_id': head['checkpoint_id'], 'revision': head['revision']}
        ck_rel = f"{rel}/checkpoints/{head['checkpoint_id']}.json"
        remote_head = show_bytes(git, root, f'{remote_ref}:{rel}/HEAD.json') if has_remote else None
        if remote_head == head_bytes and show_bytes(git, root, f'{remote_ref}:{ck_rel}') is not None:
            row['sync_state'] = 'PUSHED_VERIFIED' if result['fetched_now'] else 'PUSHED_AS_OF_LAST_FETCH'
        else:
            remote_cid = None
            if remote_head is not None:
                try:
                    parsed = json.loads(remote_head)
                    remote_cid = parsed.get('checkpoint_id')
                    row.update(remote_checkpoint_id=remote_cid, remote_revision=parsed.get('revision'))
                except ValueError:
                    pass
            local_ahead = remote_cid is None or (pdir / 'checkpoints' / f'{remote_cid}.json').exists()
            if remote_cid and show_bytes(git, root, f'{remote_ref}:{ck_rel}') is not None:
                row['sync_state'] = 'REMOTE_AHEAD'  # someone pushed a newer checkpoint: pull before writing
            elif not local_ahead:
                row['sync_state'] = 'DIVERGED'  # both sides have checkpoints the other lacks
            elif show_bytes(git, root, f'HEAD:{rel}/HEAD.json') == head_bytes:
                row['sync_state'] = 'COMMITTED_NOT_PUSHED'
            else:
                row['sync_state'] = 'LOCAL_ONLY_UNCOMMITTED'
        rows.append(row)
    result['projects'] = rows
    if not result['fetched_now']:
        result['note'] = 'Remote view is as of the last fetch; use --fetch before claiming anything is synced'
    return result


def rebase_onto_remote(root, git, branch, ident):
    """Rebase local commits onto origin. Conflicts limited to derived views are regenerated;
    anything else aborts the rebase (no data loss, no force)."""
    regenerated = []
    p = git.run(ident + ['rebase', '--quiet', f'refs/remotes/origin/{branch}'], root, check=False)
    rounds = 0
    while p.returncode != 0:
        rounds += 1
        conflicted = [c for c in git.out(['diff', '--name-only', '--diff-filter=U'], root).splitlines() if c]
        in_rebase = (Path(root) / '.git' / 'rebase-merge').exists() or (Path(root) / '.git' / 'rebase-apply').exists()
        if not conflicted and in_rebase and rounds <= 50:
            p = git.run(ident + ['rebase', '--skip'], root, check=False)  # commit became empty after regeneration
            continue
        if not conflicted or rounds > 50 or not all(DERIVED.fullmatch(c) for c in conflicted):
            git.run(['rebase', '--abort'], root, check=False)
            raise SyncError('Concurrent remote changes conflict with local commits in: ' +
                            (', '.join(conflicted) or 'unknown paths') +
                            '. Local commits are kept. Re-read the other checkpoint and write a --merge-parent '
                            'checkpoint (see references/recovery.md); never force-push.')
        for c in conflicted:
            if c != 'INDEX.md':
                pdir = handoff.project_dir(root, '/'.join(c.split('/')[1:3]))
                _, cp = handoff.checked_head(pdir)
                handoff.chain(pdir, cp)
                handoff.atomic(pdir / 'CURRENT.md', handoff.current_bytes(cp))
        handoff.atomic(Path(root) / 'INDEX.md', handoff.index_bytes(root))
        git.run(['add', '--'] + sorted(set(conflicted) | {'INDEX.md'}), root)
        regenerated += conflicted
        p = git.run(ident + ['rebase', '--continue'], root, check=False)
    return regenerated


def push(root, git, projects, extra_paths=(), message='', dry_run=False, max_file_mb=25, remote_url=None):
    root = Path(root).resolve()
    if not projects:
        raise SyncError('At least one --project is required')
    if not message.strip():
        raise SyncError('A commit message is required')
    info = repo_info(root, git, remote_url)
    branch = info['branch']
    ensure_no_operation_in_progress(root)
    for proj in projects:
        handoff.validate(root, proj)  # integrity of the whole chain; raises on failure
    paths = [f'projects/{proj}' for proj in projects] + ['INDEX.md']
    for rel in extra_paths:
        handoff.inside(root, rel)
        paths.append(Path(rel).as_posix())
    entries = status_entries(root, git)
    staged_outside = [p for xy, p in entries if xy[0] not in ' ?' and not under(p, paths)]
    if staged_outside:
        raise SyncError('Other staged paths present; unstage them or pass --path: ' + ', '.join(staged_outside[:10]))
    blocking = [p for xy, p in entries if xy != '??' and xy[1] != ' ' and not under(p, paths)]
    if blocking:
        raise SyncError('Uncommitted changes outside the selected paths would block a safe rebase: ' +
                        ', '.join(blocking[:10]) + ' (include them with --path, commit or stash them)')
    max_bytes = max_file_mb * 1024 * 1024
    ident, ident_source = identity(git, root)
    remote_exists = remote_branch_exists(git, root, branch)

    if dry_run:
        listing = git.out(['add', '-A', '--dry-run', '--'] + paths, root).splitlines()
        would, problems = [], []
        for line in listing:
            m = re.match(r"^(add|remove) '(.*)'$", line)
            if not m:
                continue
            would.append(f'{m.group(1)} {m.group(2)}')
            f = root / m.group(2)
            blob = f.read_bytes() if m.group(1) == 'add' and f.is_file() else None
            problems += scan_content(m.group(2), blob, git, max_bytes)
            if '/checkpoints/' in m.group(2) and (m.group(1) == 'remove' or show_bytes(git, root, 'HEAD:' + m.group(2))):
                problems.append(f'immutable checkpoint would change: {m.group(2)}')
        behind = None
        if remote_exists:
            git.run(['fetch', '--quiet', 'origin', branch], root)
            behind = int(git.out(['rev-list', '--count', f'HEAD..refs/remotes/origin/{branch}'], root))
        index_current = (root / 'INDEX.md').exists() and (root / 'INDEX.md').read_bytes() == handoff.index_bytes(root)
        return {'status': 'DRY_RUN', 'would_stage': would, 'problems': problems, 'behind_remote': behind,
                'index_current': index_current, 'note': 'Nothing staged, committed or pushed.'}

    regenerated_views = refresh_views(root, projects)
    git.run(['add', '-A', '--'] + paths, root)
    staged_raw = git.run(['diff', '--cached', '--name-status', '--no-renames', '-z'], root).stdout.decode('utf-8', 'replace')
    fields = [x for x in staged_raw.split('\0') if x]
    staged = list(zip(fields[0::2], fields[1::2]))
    problems = []
    for code, path in staged:
        if '/checkpoints/' in path and code[0] in 'DM':
            problems.append(f'immutable checkpoint would be {"deleted" if code[0] == "D" else "modified"}: {path}')
        blob = None if code[0] == 'D' else git.run(['cat-file', 'blob', ':' + path], root).stdout
        problems += scan_content(path, blob, git, max_bytes)
    if problems:
        git.run(['reset', '-q', '--'] + paths, root)
        raise SyncError('Refused to commit: ' + '; '.join(problems))
    committed = False
    if staged:
        git.run(ident + ['commit', '--quiet', '-m', message], root)
        committed = True

    rebased, regenerated = False, []
    if remote_exists:
        git.run(['fetch', '--quiet', 'origin', branch], root)
        if int(git.out(['rev-list', '--count', f'HEAD..refs/remotes/origin/{branch}'], root)):
            regenerated += rebase_onto_remote(root, git, branch, ident)
            rebased = True
        ahead = int(git.out(['rev-list', '--count', f'refs/remotes/origin/{branch}..HEAD'], root))
    else:
        ahead = int(git.out(['rev-list', '--count', 'HEAD'], root))
    local = git.out(['rev-parse', 'HEAD'], root)
    checkpoints = {proj: handoff.checked_head(handoff.project_dir(root, proj))[0] for proj in projects}
    if ahead == 0:
        line = git.out(['ls-remote', 'origin', f'refs/heads/{branch}'], root)
        state = 'NO_CHANGES' if line.split() and line.split()[0] == local else 'UNCERTAIN_REMOTE'
        return {'status': state, 'commit': local, 'branch': branch, 'checkpoints': checkpoints,
                'note': 'Nothing new to push; no empty commit created.', 'checked_at': utcnow()}
    for attempt in (1, 2):
        p = git.run(['push', '--quiet', 'origin', f'HEAD:refs/heads/{branch}'], root, check=False)
        if p.returncode == 0:
            break
        err = git.redact(p.stderr.decode('utf-8', 'replace'))
        if attempt == 1 and re.search(r'non-fast-forward|fetch first|rejected', err):
            git.run(['fetch', '--quiet', 'origin', branch], root)
            regenerated += rebase_onto_remote(root, git, branch, ident)
            rebased = True
            continue
        raise SyncError('push failed (local commit kept, state PENDING_SYNC): ' + err.strip()[-600:])
    local = git.out(['rev-parse', 'HEAD'], root)
    line = git.out(['ls-remote', 'origin', f'refs/heads/{branch}'], root)
    remote = line.split()[0] if line.split() else ''
    if remote != local:
        raise SyncError(f'UNCERTAIN: remote ref {remote[:12] or "missing"} != local {local[:12]} after push; '
                        're-read the remote before retrying; never force-push')
    git.run(['fetch', '--quiet', 'origin', branch], root, check=False)
    checkpoints = {proj: handoff.checked_head(handoff.project_dir(root, proj))[0] for proj in projects}
    result = {'status': 'PUSHED_VERIFIED', 'commit': local, 'branch': branch, 'new_commit': committed,
              'rebased_onto_remote': rebased, 'views_refreshed': regenerated_views,
              'derived_conflicts_regenerated': regenerated, 'checkpoints': checkpoints,
              'verified_at': utcnow(),
              'method': 'git push (no force) + git ls-remote returned the same commit id; '
                        'the commit id hashes the full tree, so remote content is byte-identical',
              'identity': ident_source}
    if info['repairs']:
        result['repairs'] = info['repairs']
    if info['origin_url_has_credentials']:
        result['warning'] = 'origin URL embeds credentials (redacted); switch to a clean URL plus GH_TOKEN'
    return result


def pull(root, git, remote_url=None):
    """Bring the clone up to date without losing local commits: fast-forward, or rebase local commits
    (derived-view conflicts are regenerated; anything else aborts cleanly). Never discards work."""
    root = Path(root).resolve()
    info = repo_info(root, git, remote_url)
    branch = info['branch']
    ensure_no_operation_in_progress(root)
    base = {'branch': branch, 'repairs': info['repairs'], 'checked_at': utcnow()}
    if not remote_branch_exists(git, root, branch):
        return dict(base, status='NO_REMOTE_BRANCH', note='remote has no such branch yet; push will create it')
    git.run(['fetch', '--quiet', 'origin', branch], root)
    remote_ref = f'refs/remotes/origin/{branch}'
    has_head = git.run(['rev-parse', '--verify', '--quiet', 'HEAD'], root, check=False).returncode == 0
    if not has_head:  # empty local repository: adopt the remote branch
        git.run(['reset', '--quiet', '--hard', remote_ref], root)
        return dict(base, status='UPDATED', commit=git.out(['rev-parse', 'HEAD'], root))
    behind, ahead = (int(x) for x in git.out(['rev-list', '--left-right', '--count', f'{remote_ref}...HEAD'], root).split())
    if behind == 0:
        return dict(base, status='UP_TO_DATE', commit=git.out(['rev-parse', 'HEAD'], root), local_commits_not_pushed=ahead)
    dirty = [p for xy, p in status_entries(root, git) if xy != '??']
    if dirty:
        raise SyncError('Uncommitted tracked changes block pull: ' + ', '.join(dirty[:10]) +
                        ' (push them first with progress_sync.py push, or stash them)')
    ident, _ = identity(git, root)
    regenerated = []
    if ahead == 0:
        git.run(['merge', '--ff-only', '--quiet', remote_ref], root)
    else:
        regenerated = rebase_onto_remote(root, git, branch, ident)
    return dict(base, status='UPDATED', commit=git.out(['rev-parse', 'HEAD'], root), pulled_commits=behind,
                local_commits_rebased=ahead, derived_conflicts_regenerated=regenerated,
                note='local commits are rebased but not pushed yet' if ahead else None)


def save(root, git, project, expected, patch, note, message=None, extra_paths=(), dry_run=False, remote_url=None):
    """End-of-turn in one step: handoff update (expected-HEAD protected) + verified push."""
    written = handoff.update(root, project, patch, expected, note, dry_run=dry_run)
    if dry_run:
        return {'status': 'DRY_RUN', 'update': written}
    try:
        synced = push(root, git, [project], extra_paths, message or f'{project}: {note}'[:120], remote_url=remote_url)
    except (SyncError, handoff.HandoffError) as e:
        raise SyncError(f"checkpoint {written['checkpoint_id']} (rev {written['revision']}) is written locally but "
                        f'NOT synced (LOCAL_ONLY); fix the cause and run push again: {e}')
    return {'status': synced['status'], 'checkpoint': {k: written[k] for k in ('checkpoint_id', 'revision', 'changed_keys', 'lint')},
            'sync': synced}


def _skill_heads(git, skill_dir, root):
    """(local skill HEAD, remote main of the public skill repo, pinned head in LOCATION.json)."""
    local = git.out(['rev-parse', 'HEAD'], skill_dir, check=False) if (Path(skill_dir) / '.git').exists() else ''
    try:
        loc = json.loads((Path(root) / 'LOCATION.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        loc = {}
    pinned = (loc.get('public_rule_heads') or {}).get('agent-progress-skill', '')
    url = loc.get('remote_entry_url') or 'https://github.com/defidehathorn389-max/agent-progress-skill'
    line = git.out(['ls-remote', url.rstrip('/') + ('' if url.endswith('.git') else '.git'), 'refs/heads/main'], skill_dir, check=False)
    return local, (line.split()[0] if line.split() else ''), pinned, url


def doctor(root, git, token_present, remote_url=None, skill_dir=None):
    """Start-of-session health check. Read-only except for re-adding a missing origin."""
    root = Path(root)
    checks = []

    def add(name, ok, detail, fix=None, advisory=False):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail, **({'fix': fix} if fix and not ok else {}),
                       **({'advisory': True} if advisory else {})})

    add('git', shutil.which('git') is not None, 'git command available', 'install git')
    add('credentials', token_present, 'token available via GH_TOKEN/GITHUB_TOKEN/--token-file (value not shown)',
        'save the token outside the repository and pass --token-file, or export GH_TOKEN')
    if not (root / '.git').exists():
        add('clone', False, f'{root} is not a git clone', 'progress_sync.py --token-file F clone --repo OWNER/agent-progress')
        return {'ok': False, 'checks': checks}
    branch = None
    try:
        info = repo_info(root, git, remote_url)
        branch = info['branch']
        add('origin', True, 'origin re-added from LOCATION.json (snapshot had dropped .git/config)' if info['repairs'] else 'origin present')
        if info['origin_url_has_credentials']:
            add('origin url', False, 'origin URL embeds credentials', 'git remote set-url origin https://github.com/OWNER/agent-progress.git')
    except SyncError as e:
        add('origin', False, str(e), 'pass --remote-url https://github.com/OWNER/agent-progress.git')
    add('identity', True, identity(git, root)[1])
    if branch:
        try:
            if remote_branch_exists(git, root, branch):
                git.run(['fetch', '--quiet', 'origin', branch], root)
                behind, ahead = (int(x) for x in git.out(['rev-list', '--left-right', '--count',
                                                            f'refs/remotes/origin/{branch}...HEAD'], root).split())
                add('remote', behind == 0, f'ahead {ahead}, behind {behind}', 'progress_sync.py pull')
                if ahead:
                    add('unpushed commits', False, f'{ahead} local commit(s) not on the remote', 'progress_sync.py push --project ...')
            else:
                add('remote', True, 'remote branch does not exist yet (first push will create it)')
        except SyncError as e:
            add('remote', False, str(e), 'check network access and token scope (Contents: read/write)')
    dirty = [p for xy, p in status_entries(root, git) if xy != '??']
    add('working tree', not dirty, f'{len(dirty)} uncommitted tracked path(s)' + (': ' + ', '.join(dirty[:5]) if dirty else ''),
        'push the affected projects (progress_sync.py push) or stash')
    try:
        v = handoff.validate(root)
        stale = [x['project'] for x in v['projects'] if x['warnings']]
        add('integrity', True, f"{len(v['projects'])} project(s), full chains verified")
        add('derived views', v['index_current'] and not stale, 'INDEX/CURRENT current' if v['index_current'] and not stale
            else f'stale: {stale or "INDEX.md"}', 'handoff.py rebuild, then push')
        lint = {x['project']: len(x['lint']) for x in v['projects'] if x['lint']}
        add('lint', not lint, 'no advisories' if not lint else f'advisories: {lint}',
            'see handoff.py validate --project P; fix during that project\'s next checkpoint', advisory=True)
    except handoff.HandoffError as e:
        add('integrity', False, str(e), 'see references/recovery.md; do not overwrite HEAD')
    if skill_dir:
        local, remote, pinned, url = _skill_heads(git, skill_dir, root)
        ok = bool(local) and (not remote or local == remote)
        add('skill version', ok, f'local {local[:7] or "?"}, remote {remote[:7] or "?"}, LOCATION pin {pinned[:7] or "?"}',
            f'update the skill clone: git -C {skill_dir} pull --ff-only {url} main (or re-clone)')
    return {'ok': all(c['ok'] or c.get('advisory') for c in checks), 'checks': checks, 'checked_at': utcnow()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=handoff.DEFAULT_ROOT)
    ap.add_argument('--token-file', type=Path, help='file outside the repository holding the token')
    ap.add_argument('--remote-url', help='progress repository URL used to re-add a missing origin '
                                         '(default: LOCATION.json progress_remote_url)')
    sub = ap.add_subparsers(dest='command', required=True)
    c = sub.add_parser('clone')
    src = c.add_mutually_exclusive_group(required=True)
    src.add_argument('--repo', help='owner/name on github.com')
    src.add_argument('--url', help='other clean remote URL or local path (tests/mirrors)')
    c.add_argument('--dest', type=Path, help='defaults to --root')
    c.add_argument('--depth', type=int)
    s = sub.add_parser('status')
    s.add_argument('--fetch', action='store_true')
    s.add_argument('--project', action='append')
    sub.add_parser('doctor')
    sub.add_parser('pull')
    v = sub.add_parser('save')
    v.add_argument('--project', required=True)
    v.add_argument('--expected', required=True)
    v.add_argument('--patch', required=True, help='patch JSON file, or - for stdin')
    v.add_argument('--note', required=True)
    v.add_argument('-m', '--message')
    v.add_argument('--path', action='append', default=[])
    v.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('push')
    p.add_argument('--project', action='append', required=True)
    p.add_argument('--path', action='append', default=[], help='extra repository-relative path to commit')
    p.add_argument('-m', '--message', required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--max-file-mb', type=int, default=25)
    a = ap.parse_args(argv)
    git = None
    try:
        token = read_token(a.token_file, a.root)
        git = Git(token)
        if a.command == 'clone':
            result = clone(git, a.dest or a.root, a.repo, a.url, a.depth)
        elif a.command == 'status':
            result = status(a.root, git, a.fetch, a.project, a.remote_url)
        elif a.command == 'pull':
            result = pull(a.root, git, a.remote_url)
        elif a.command == 'doctor':
            result = doctor(a.root, git, bool(token), a.remote_url, Path(__file__).resolve().parents[1])
        elif a.command == 'save':
            result = save(a.root, git, a.project, a.expected, handoff.load_input(a.patch), a.note, a.message,
                          a.path, a.dry_run, a.remote_url)
        else:
            result = push(a.root, git, a.project, a.path, a.message, a.dry_run, a.max_file_mb, a.remote_url)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if a.command == 'doctor':
            return 0 if result['ok'] else 1
        return 0 if result.get('status') not in {'UNCERTAIN_REMOTE'} else 1
    except (SyncError, handoff.HandoffError, OSError, ValueError, subprocess.TimeoutExpired) as e:
        text = str(e) if isinstance(e, (SyncError, handoff.HandoffError)) else type(e).__name__
        print('Sync failed: ' + (git.redact(text) if git else text), file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - last-resort guard without leaking data
        print('Sync failed unexpectedly: ' + type(e).__name__, file=sys.stderr)
        return 2
    finally:
        if git:
            git.close()


if __name__ == '__main__':
    raise SystemExit(main())
