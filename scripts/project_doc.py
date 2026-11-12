#!/usr/bin/env python3
"""Per-project brief: projects/<domain>/<project>/PROJECT.md (stdlib only, no network).

Every project gets the same file name and the same five sections, so a new agent can open
any project the same way:

  ## 最后更新  newest date of the HEAD checkpoint and the journal day files
  ## 现状      status, goal, who it waits for, last change
  ## 入口文件  artifacts, repositories and paths named in the state, the progress files
  ## 下一步    first action, pending items, next actions
  ## 注意事项  constraints, things to avoid, risks, lessons

A section with nothing in it reads 「无」. PROJECT.md is generated from the HEAD checkpoint,
like CURRENT.md: change the state with handoff.py, then run `project_doc.py build`.
CURRENT.md stays the full derived view; PROJECT.md is the short card.

Commands (every command accepts --root; default $AGENT_PROGRESS_ROOT or ~/agent-progress):
  build [--project D/P]   write PROJECT.md for one project or for all
  check                   exit 1 when a PROJECT.md is missing or older than its HEAD
  index                   print a Markdown index: project and path of its PROJECT.md
"""
import argparse
import glob
import json
import os
import sys

FILE = "PROJECT.md"
MARK = "<!-- project_doc checkpoint: {} -->"
MAXLEN = 240
PATH_KEYS = (
    "repo", "skill_repo", "asset_repo", "business_repository", "source_remote", "local_source",
    "work_dir", "demo_file", "script_file", "env_doc", "final_render", "pipeline", "placement",
)


def short(v, n=MAXLEN):
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False)
    v = " ".join(v.split())
    return v if len(v) <= n else v[: n - 1] + "…"


def projects(root):
    for h in sorted(glob.glob(os.path.join(root, "projects", "*", "*", "HEAD.json"))):
        d = os.path.dirname(h)
        yield os.path.relpath(d, os.path.join(root, "projects")).replace(os.sep, "/"), d


def head(d):
    cid = json.load(open(os.path.join(d, "HEAD.json"), encoding="utf-8"))["checkpoint_id"]
    cp = json.load(open(os.path.join(d, "checkpoints", cid + ".json"), encoding="utf-8"))
    return cid, cp


def bullets(items):
    items = [i for i in items if i]
    return "\n".join("- " + i for i in items) if items else "无"


def item_text(x):
    if isinstance(x, str):
        return short(x)
    if not isinstance(x, dict):
        return short(x)
    main = x.get("action") or x.get("task") or x.get("risk") or x.get("item") or x.get("text") or ""
    extra = []
    if x.get("next_action") and x.get("next_action") != main:
        extra.append("下一步：" + str(x["next_action"]))
    if x.get("blocked_by"):
        extra.append("阻塞：" + str(x["blocked_by"]))
    if x.get("mitigation"):
        extra.append("应对：" + str(x["mitigation"]))
    if x.get("requires_user"):
        extra.append("需用户")
    s = short(main)
    if extra:
        s += "（" + short("；".join(extra), 160) + "）"
    if x.get("id"):
        s = "**[{}]** {}".format(x["id"], s)
    return s


def render(name, d, cid, cp):
    s = cp.get("state", {})
    h = s.get("handoff") or {}
    ctx = s.get("context") or {}
    title = s.get("title") or name
    note = cp.get("note") or cp.get("change_note") or ""
    updated = (cp.get("updated_at") or cp.get("created_at") or "")[:10]
    days = sorted(os.path.basename(f)[:10] for f in glob.glob(os.path.join(d, "journal", "days", "*.md")))
    last = max([x for x in (updated, days[-1] if days else "") if x] or ["?"])
    last_line = "{}（检查点 {}；日记录 {}）".format(last, updated or "无", days[-1] if days else "无")

    now = [
        "状态：**{}**".format(s.get("status", "?")),
        "目标：" + short(s.get("goal", "")) if s.get("goal") else "",
        "等谁：" + short(h.get("waiting_for")) if h.get("waiting_for") else "",
        "最近变化：" + short(note) if note else "",
    ]
    if h.get("running_operations"):
        now.append("运行中：" + short(h["running_operations"]))

    entry = []
    for a in s.get("artifacts") or []:
        if not isinstance(a, dict):
            entry.append(short(a))
            continue
        where = a.get("local_path") or ""
        r = a.get("remote")
        if isinstance(r, dict):
            rr = (r.get("repo") or r.get("repository") or "").split("@")[0] + (":" + r["path"] if r.get("path") else "")
            where = " · ".join(x for x in (where, "远端 " + rr if rr.strip(":") else "") if x)
        elif isinstance(r, str):
            r = r.split("@")[0]
            where = " · ".join(x for x in (where, "远端 " + r) if x)
        entry.append("{}{}".format(short(where, 200) or a.get("id", ""), "（" + short(a["role"], 60) + "）" if a.get("role") else ""))
    for k in PATH_KEYS:
        if ctx.get(k):
            entry.append("{}：{}".format(k, short(ctx[k], 200)))
    entry.append("完整状态：`CURRENT.md`；历史：`journal/`（`journal.py recent --project {}`）".format(name))

    nxt = []
    if h.get("first_action"):
        nxt.append("接手第一步：" + short(h["first_action"]))
    nxt += ["未完成 " + item_text(p) for p in s.get("pending") or []]
    nxt += ["计划 " + item_text(p) for p in s.get("next_actions") or []]

    care = [item_text(c) for c in s.get("constraints") or []]
    care += ["别做：" + item_text(c) for c in s.get("avoid") or []]
    care += ["风险：" + item_text(c) for c in s.get("risks") or []]
    ll = ctx.get("lessons_learned")
    if isinstance(ll, list):
        care += ["教训：" + short(x) for x in ll]
    elif ll:
        care.append("教训：" + short(ll))

    out = [
        "# {}".format(title),
        "",
        "项目 `{}` · 检查点 `{}`".format(name, cid),
        "",
        "> 生成文件（agent-progress-skill/scripts/project_doc.py），勿手改：改状态走 handoff.py，再跑 `project_doc.py build --project {}`。".format(name),
        "",
        "## 最后更新", "", last_line, "",
        "## 现状", "", bullets(now), "",
        "## 入口文件", "", bullets(entry), "",
        "## 下一步", "", bullets(nxt), "",
        "## 注意事项", "", bullets(care), "",
        MARK.format(cid), "",
    ]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("AGENT_PROGRESS_ROOT", os.path.expanduser("~/agent-progress")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--project")
    sub.add_parser("check")
    sub.add_parser("index")
    a = ap.parse_args()

    bad = 0
    for name, d in projects(a.root):
        if a.cmd == "build" and a.project and name != a.project:
            continue
        path = os.path.join(d, FILE)
        if a.cmd == "index":
            print("- {} → agent-progress/projects/{}/{}".format(name, name, FILE))
            continue
        try:
            cid, cp = head(d)
        except (OSError, KeyError, ValueError) as e:
            print("SKIP {}: {}".format(name, e), file=sys.stderr)
            bad += 1
            continue
        if a.cmd == "build":
            text = render(name, d, cid, cp)
            old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            if old != text:
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(path + ".tmp", path)
                print("WROTE", name)
        else:
            ok = os.path.exists(path) and MARK.format(cid) in open(path, encoding="utf-8").read()
            if not ok:
                print("STALE" if os.path.exists(path) else "MISSING", name)
                bad += 1
    if a.cmd == "check":
        print("PROJECT_DOCS_OK" if not bad else "PROJECT_DOCS_STALE {}".format(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
