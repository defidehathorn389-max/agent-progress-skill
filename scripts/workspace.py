#!/usr/bin/env python3
"""Sandbox storage manager (standard library + git).

Arena-style sandboxes keep only a snapshot of the workspace between turns (about 128 MB or 10,000
files; some directory names and .git/config are never kept) and often restart between turns, while
the disk itself has ~20 GB for work inside a turn. So use two layers:

  persisted area  /home/user                 small things that must survive: slim clones of the
                                             progress/memory/skills repositories, the rules repo, the token
  work area       /home/user/.cache/work/<n> heavy project files for this turn; never snapshotted,
                                             fetched on demand (blobless + sparse), gone after a restart

Everything that matters is pushed to GitHub before the reply ends (progress: save, memory: sync,
project files: park --push or the project's own push).

Commands:
  status                              persisted footprint vs the snapshot cap, biggest directories, advice
  slim   [--root DIR]                 turn the agent-progress clone into a slim clone (history without old
                                      file contents; no evidence/ media, no legacy sync-receipts/);
                                      verified (same commit, validate) before the swap
  open   NAME (--repo O/R | --url U) [--path P ...] [--ref BRANCH]
                                      lightweight clone of just the needed paths into the work area
  park   [--push] [-m MESSAGE]        work-area repositories with uncommitted/unpushed work (optionally push)

Environment: AGENT_WORKSPACE_HOME (default /home/user), AGENT_WORK_ROOT (default <home>/.cache/work).
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff  # noqa: E402
import progress_sync as ps  # noqa: E402

CAP_BYTES, CAP_FILES = 128_000_000, 10_000
WARN_RATIO, FAIL_RATIO = 0.70, 0.85
EXCLUDED = {'.arena', '.cache', '.local', '.mypy_cache', '.next', '.nox', '.npm', '.nuxt', '.output', '.parcel-cache',
            '.pytest_cache', '.ruff_cache', '.svelte-kit', '.tox', '.turbo', '.venv', '.vite', '__pycache__', 'build',
            'coverage', 'dist', 'node_modules', 'out', 'target'}


class WorkspaceError(Exception):
    pass


def home():
    return Path(os.environ.get('AGENT_WORKSPACE_HOME') or '/home/user')


def work_root():
    return Path(os.environ.get('AGENT_WORK_ROOT') or home() / '.cache' / 'work')


def du(path):
    size = files = 0
    for dp, dn, fn in os.walk(path):
        for f in fn:
            try:
                size += os.lstat(os.path.join(dp, f)).st_size
                files += 1
            except OSError:
                pass
    return size, files


def footprint(root=None, top=8):
    """What the snapshot would try to keep: everything under home except excluded directory names
    and .git/config."""
    root = Path(root or home())
    size = files = 0
    groups = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDED and not os.path.islink(os.path.join(dp, d))]
        for f in fn:
            p = os.path.join(dp, f)
            if p.endswith(os.sep + '.git' + os.sep + 'config'):
                continue
            try:
                st = os.lstat(p)
            except OSError:
                continue
            size += st.st_size
            files += 1
            rel = os.path.relpath(p, root).split(os.sep)
            key = '/'.join(rel[:2]) if len(rel) > 2 else rel[0]
            groups[key] = groups.get(key, 0) + st.st_size
    ratio = max(size / CAP_BYTES, files / CAP_FILES)
    level = 'ok' if ratio < WARN_RATIO else 'warn' if ratio < FAIL_RATIO else 'fail'
    advice = []
    if level != 'ok':
        if groups.get('agent-progress/evidence', 0) > 5_000_000 or groups.get('agent-progress/.git', 0) > 10_000_000:
            advice.append('workspace.py slim  # progress clone without evidence media and old file contents')
        advice.append(f'move large working files to {work_root()} (not snapshotted) and push results to GitHub')
    wr = work_root()
    work_bytes = du(wr)[0] if wr.exists() else 0
    return {'bytes': size, 'files': files, 'cap_bytes': CAP_BYTES, 'cap_files': CAP_FILES,
            'size_ratio': round(size / CAP_BYTES, 3), 'files_ratio': round(files / CAP_FILES, 3), 'level': level,
            'biggest': [{'path': k, 'bytes': v} for k, v in sorted(groups.items(), key=lambda kv: -kv[1])[:top]],
            'work_area': str(wr), 'work_area_bytes': work_bytes, 'advice': advice}


def _is_slim(root, git):
    return (git.out(['config', '--get', 'remote.origin.promisor'], root, check=False) == 'true' and
            git.out(['config', '--get', 'core.sparseCheckout'], root, check=False) == 'true')


def slim(root, token=None):
    """Replace a full progress clone by a verified slim clone. Refuses when anything is not pushed."""
    root = Path(root).resolve()
    git = ps.Git(token)
    try:
        info = ps.repo_info(root, git)
        branch = info['branch']
        if _is_slim(root, git):
            return {'status': 'ALREADY_SLIM', 'root': str(root)}
        entries = ps.status_entries(root, git)
        if entries:
            raise WorkspaceError('uncommitted or untracked files would be lost: ' + ', '.join(p for _, p in entries[:10]) +
                                 ' (push them with progress_sync.py, or remove them) before slimming')
        git.run(['fetch', '--quiet', 'origin', branch], root)
        if int(git.out(['rev-list', '--count', f'refs/remotes/origin/{branch}..HEAD'], root)):
            raise WorkspaceError('local commits are not pushed yet; push first, then slim')
        before = du(root)
        head = git.out(['rev-parse', 'HEAD'], root)
        url = git.out(['remote', 'get-url', 'origin'], root)
        tmp = root.parent / (root.name + '.slim-tmp')
        backup = root.parent / (root.name + '.full-backup')
        for leftover in (tmp, backup):
            if leftover.exists():
                shutil.rmtree(leftover)
        ps.clone(git, tmp, url=url, slim=True)
        if git.out(['rev-parse', 'HEAD'], tmp) != head:
            shutil.rmtree(tmp)
            raise WorkspaceError('slim clone is not at the same commit; nothing changed (pull and retry)')
        try:
            handoff.validate(tmp)
        except handoff.HandoffError as e:
            shutil.rmtree(tmp)
            raise WorkspaceError(f'slim clone failed validation, nothing changed: {e}')
        os.rename(root, backup)
        try:
            os.rename(tmp, root)
        except OSError:
            os.rename(backup, root)
            raise
        shutil.rmtree(backup)
        after = du(root)
        return {'status': 'SLIMMED', 'root': str(root), 'commit': head,
                'before': {'bytes': before[0], 'files': before[1]}, 'after': {'bytes': after[0], 'files': after[1]},
                'note': 'evidence/ and sync-receipts/ stay on GitHub; fetch one when needed with '
                        'git -C <root> sparse-checkout add /evidence/<path>'}
    finally:
        git.close()


def open_repo(name, repo=None, url=None, paths=(), ref=None, token=None):
    """Lightweight clone (history without old contents, only `paths`) into the work area."""
    if not name or '/' in name or name.startswith('.'):
        raise WorkspaceError('name must be a simple folder name')
    if repo and not ps.REPO_RE.fullmatch(repo):
        raise WorkspaceError('repo must look like owner/name')
    url = url or f'https://github.com/{repo}.git'
    for p in paths:
        handoff.safe_relative(p)
    # cone mode: whole directories (a file path pulls in its folder); root-level files always come along
    dirs = sorted({(p.strip('/') if not Path(p).suffix else str(Path(p.strip('/')).parent)) for p in paths} - {'', '.'})
    dest = work_root() / name
    git = ps.Git(token)
    try:
        if (dest / '.git').exists():
            ps.ensure_origin(dest, git, url)
            if dirs:
                git.run(['sparse-checkout', 'add'] + dirs, dest)
            pulled = ps.pull(dest, git, url)
            status = 'UPDATED'
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            git.run(['clone', '--quiet', '--filter=blob:none', '--no-checkout'] + (['--branch', ref] if ref else []) +
                    [url, str(dest)], dest.parent)
            if dirs:
                git.run(['sparse-checkout', 'set', '--cone'] + dirs, dest)
            branch = git.out(['symbolic-ref', '--quiet', '--short', 'HEAD'], dest, check=False)
            if branch:
                git.run(['checkout', '--quiet', branch], dest)
            pulled, status = None, 'OPENED'
        size, files = du(dest)
        return {'status': status, 'dir': str(dest), 'commit': git.out(['rev-parse', '--verify', '--quiet', 'HEAD'], dest, check=False),
                'bytes': size, 'files': files, 'paths': list(paths) or ['(whole repository)'],
                'note': 'work area: not kept between turns; push results before the reply ends (workspace.py park --push)'}
    finally:
        git.close()


def park(push=False, message='', token=None):
    rows = []
    wr = work_root()
    git = ps.Git(token)
    try:
        for d in sorted(wr.iterdir()) if wr.exists() else []:
            if not d.is_dir():
                continue
            size = du(d)[0]
            if not (d / '.git').exists():
                rows.append({'name': d.name, 'state': 'not-a-repository', 'bytes': size,
                             'advice': 'lost at the next restart: upload what you need or make it reproducible'})
                continue
            dirty = [p for _, p in ps.status_entries(d, git)]
            branch = git.out(['symbolic-ref', '--quiet', '--short', 'HEAD'], d, check=False)
            has_remote = git.run(['rev-parse', '--verify', '--quiet', f'refs/remotes/origin/{branch}'], d, check=False).returncode == 0
            ahead = int(git.out(['rev-list', '--count', f'refs/remotes/origin/{branch}..HEAD'], d) or 0) if has_remote else 0
            row = {'name': d.name, 'state': 'clean' if not dirty and not ahead else 'unpushed', 'bytes': size,
                   'uncommitted': len(dirty), 'unpushed_commits': ahead}
            if push and row['state'] == 'unpushed':
                result = ps.push_paths(d, git, ['.'], message or f'{d.name}: work in progress (parked before the turn ended)')
                row.update(state=result['status'], commit=result.get('commit'))
            rows.append(row)
    finally:
        git.close()
    return {'work_area': str(wr), 'repos': rows, 'unpushed': [r['name'] for r in rows if r['state'] in ('unpushed', 'not-a-repository')],
            'note': 'the work area is not kept between turns; anything listed as unpushed is lost at the next restart'}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--token-file', type=Path)
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    s = sub.add_parser('slim')
    s.add_argument('--root', type=Path, default=handoff.DEFAULT_ROOT)
    o = sub.add_parser('open')
    o.add_argument('name')
    g = o.add_mutually_exclusive_group(required=True)
    g.add_argument('--repo')
    g.add_argument('--url')
    o.add_argument('--path', action='append', default=[])
    o.add_argument('--ref')
    p = sub.add_parser('park')
    p.add_argument('--push', action='store_true')
    p.add_argument('-m', '--message', default='')
    a = ap.parse_args(argv)
    token = None
    if a.token_file:
        token = a.token_file.read_text(encoding='utf-8').strip() or None
    token = token or os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    try:
        if a.command == 'status':
            result = footprint()
        elif a.command == 'slim':
            result = slim(a.root, token)
        elif a.command == 'open':
            result = open_repo(a.name, a.repo, a.url, a.path, a.ref, token)
        else:
            result = park(a.push, a.message, token)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if a.command == 'status':
            return 0 if result['level'] != 'fail' else 1
        if a.command == 'park':
            return 0 if not result['unpushed'] else 1
        return 0
    except (WorkspaceError, ps.SyncError, handoff.HandoffError, OSError, ValueError) as e:
        print('Workspace failed: ' + handoff.SECRET_RE.sub('***', str(e))[:600], file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
