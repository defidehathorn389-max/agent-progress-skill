#!/usr/bin/env python3
"""Git sync for the private agent-progress repository (v2, standard library only).

  clone  --repo OWNER/NAME [--dest DIR] [--depth N]
  status [--fetch] [--project D/P ...]
  push   --project D/P [--project ...] [--path REL ...] -m MESSAGE [--dry-run]

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

def repo_info(root, git):
    root = Path(root)
    if not (root / '.git').exists():
        raise SyncError('Not a git repository: ' + str(root))
    branch = git.out(['symbolic-ref', '--quiet', '--short', 'HEAD'], root, check=False)
    if not branch:
        raise SyncError('Detached HEAD: check out the default branch first')
    url = git.out(['remote', 'get-url', 'origin'], root, check=False)
    return {'branch': branch, 'origin_url_has_credentials': bool(USERINFO.search(url))}


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


def identity_args(git, root):
    configured = git.run(['config', 'user.email'], root, check=False)
    return [] if configured.returncode == 0 and configured.stdout.strip() else FALLBACK_IDENTITY


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
    if dest.exists() and any(dest.iterdir()):
        raise SyncError('Destination exists and is not empty: ' + str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ['clone', '--quiet'] + (['--depth', str(depth)] if depth else []) + [url, str(dest)]
    git.run(args, dest.parent)
    return {'status': 'CLONED', 'dest': str(dest),
            'commit': git.out(['rev-parse', '--verify', '--quiet', 'HEAD'], dest, check=False) or None,
            'branch': git.out(['symbolic-ref', '--quiet', '--short', 'HEAD'], dest, check=False)}


def status(root, git, fetch=False, projects=None):
    root = Path(root)
    info = repo_info(root, git)
    branch = info['branch']
    result = {'branch': branch, 'fetched_now': False, 'checked_at': utcnow()}
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


def push(root, git, projects, extra_paths=(), message='', dry_run=False, max_file_mb=25):
    root = Path(root).resolve()
    if not projects:
        raise SyncError('At least one --project is required')
    if not message.strip():
        raise SyncError('A commit message is required')
    info = repo_info(root, git)
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
    ident = identity_args(git, root)
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
                        'the commit id hashes the full tree, so remote content is byte-identical'}
    if info['origin_url_has_credentials']:
        result['warning'] = 'origin URL embeds credentials (redacted); switch to a clean URL plus GH_TOKEN'
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', type=Path, default=handoff.DEFAULT_ROOT)
    ap.add_argument('--token-file', type=Path, help='file outside the repository holding the token')
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
    p = sub.add_parser('push')
    p.add_argument('--project', action='append', required=True)
    p.add_argument('--path', action='append', default=[], help='extra repository-relative path to commit')
    p.add_argument('-m', '--message', required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--max-file-mb', type=int, default=25)
    a = ap.parse_args(argv)
    git = None
    try:
        git = Git(read_token(a.token_file, a.root))
        if a.command == 'clone':
            result = clone(git, a.dest or a.root, a.repo, a.url, a.depth)
        elif a.command == 'status':
            result = status(a.root, git, a.fetch, a.project)
        else:
            result = push(a.root, git, a.project, a.path, a.message, a.dry_run, a.max_file_mb)
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
