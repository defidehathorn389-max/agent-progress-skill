# 状态与补丁规范

## 文件

```text
agent-progress/
  GLOBAL.md  LOCATION.json  INDEX.md(派生)  EVIDENCE_MAP.json(可选)  ALIASES.json(可选：项目别名，供 find 使用)
  projects/<domain>/<id>/
    HEAD.json                     {"schema_version":1,"checkpoint_id":…,"sha256":…,"revision":N}
    CURRENT.md                    派生视图（勿手改）
    checkpoints/<id>.json         不可变完整快照：schema_version, checkpoint_id, parents[≤2]{checkpoint_id,sha256},
                                  revision, domain, project, updated_at, note, state
    evidence/ …                   可选：本项目的文本证据（大媒体放素材库）
```

`<domain>` 和 `<id>` 必须是小写 ASCII slug（`[a-z0-9][a-z0-9-]{0,63}`）。检查点 ID 的格式为 `YYYYMMDDTHHMMSSZ-<12位hex>`。revision 等于父检查点 revision 的最大值加 1。

## state 字段

| 字段 | 类型 | 必填 | 规则 / 写法 |
|---|---|---|---|
| title | 字符串 | ✓ | 非空；可以改名，项目 ID 不变 |
| status | 枚举 | ✓ | `IN_PROGRESS` `READY_FOR_REVIEW` `COMPLETED` `BLOCKED` `PAUSED`（`LOCAL_READY_PENDING_SYNC` 已弃用） |
| goal | 字符串 | ✓ | 用户目标 |
| constraints | 数组 | ✓ | 当前有效的约束，建议注明来源 |
| completed | 数组 | ✓ | `{item, evidence:[证据ID…]}`，证据 ID 必须存在 |
| pending | 数组 | ✓ | `{id, action, blocked_by, done_when}` |
| decisions | 数组 | ✓ | `{id, decision, source, scope, supersedes?}` |
| artifacts | 数组 | ✓ | `{id, role, availability: LOCAL/REMOTE_ONLY/LOCAL_AND_REMOTE, sha256(64 hex), local_path(非 REMOTE_ONLY 时必填，安全相对路径), remote(非 LOCAL 时必填)}` |
| evidence | 数组 | ✓ | `{id, kind, source, scope, limitations?}`，ID 唯一 |
| risks | 数组 | ✓ | 字符串或 `{risk, mitigation}` |
| next_actions | 数组 | ✓ | 具体动作，或 `{action, priority, requires_user}` |
| avoid | 数组 | ✓ | 不要重复的错误 |
| sync | 对象 | ✓ | 只写“写入时已知”的外部状态，例如素材库提交；本检查点的推送状态不写在这里 |
| handoff | 对象 | ✓ | `first_action`（必填，非空）、`running_operations`（必填数组）；可选 `waiting_for`、`current_request_type`、`user_question`、`last_actor` |
| related_projects | 数组 | ✓ | `domain/id` 字符串 |
| context | 对象 |  | 可选：领域细节（当前集数、工作文件、审批标志等）；`aliases` 列出用户对项目的常用叫法，供 `find` 匹配（也可以集中写在根目录 `ALIASES.json`：`{"projects": {"domain/id": ["别名", …]}}`） |

禁止出现的字段名（任意层级）：password、passphrase、token、secret、api_key、access_token、authorization_header、private_key。字符串会扫描常见密钥格式，包括 GitHub、AWS、私钥、Bearer、Anthropic、OpenAI project、Google API、Slack、GitLab。state 序列化后不得超过 64 KiB。

## 补丁（`handoff.py update --patch FILE|-`）

```json
{
  "set":    {"点分路径": 值},
  "unset":  ["点分路径（只能删对象内部的键，不能删顶层字段）"],
  "remove": {"数组路径": ["对象的 id，或字符串原文"]},
  "upsert": {"数组路径": [{"id": "…", "…": "…"}]},
  "append": {"数组路径": [项, …]}
}
```

- 执行顺序固定为 set → unset → remove → upsert → append。
- 点分路径的第一段必须是 state 字段名。路径中间缺少的对象会自动创建，例如 `context.episode`。
- `remove` 没有匹配到任何项时报错，防止拼错 ID；`upsert` 按 id 原位替换，找不到就追加。
- 结果必须通过全部 state 规则；没有产生变化会报错，因此不会出现空检查点。
- `--dry-run` 输出变化的字段、结果大小和提示，不写入。
- `--expected` 必须等于当前 HEAD，否则报“Stale expected HEAD”。

## 提示（lint，不阻断写入）

| 提示 | 处理 |
|---|---|
| state 超过 48 KiB | 写精简检查点，并设置 `context.compacted_from` |
| handoff 超过 15 个键 | 把领域细节移到 `context` |
| COMPLETED 但仍有 pending | 关闭待办，或把状态改回进行中 |
| 进行中却没有 pending 或 next_actions | 补上明确的下一步 |
| 使用已弃用的状态 / `sync.memory` | 同步状态改用 `progress_sync.py status` 查看 |
| pending 项缺 id 或 done_when | 补完成判据 |

## 精简（`handoff.py compact`）

先 `--dry-run` 查看前后大小、各字段体积和精简后的提示，再去掉 `--dry-run` 执行（需要 `--expected`）：

- 不丢信息的移动：`handoff` 里核心键以外的键移到 `context.handoff_details`；
- 只从工作状态里修剪（父检查点仍保留完整内容）：`completed` 只保留最近 N 项（`--keep-completed`，默认 20）；删去已被取代（`supersedes`）的决定；证据只保留仍被引用的，加上最近 N 项（`--keep-evidence`）；去掉 `sync.memory`；
- `--externalize-artifacts`：把整个产物列表写进 `projects/<p>/evidence/compaction/artifacts-<父ID>.json`，状态里只留一条用 SHA256 固定的清单项（`--verify-local` 可核对）；
- 写入 `context.compacted_from`（父检查点 ID）和 `context.compaction`（修剪统计）。父检查点本身就是完整归档。

## 兼容性

v2 读取 v1 的全部检查点，无需迁移（schema_version 仍为 1）。v2 生成的 `CURRENT.md` 更易读，旧仓库首次使用时运行一次 `rebuild` 即可。只有在状态里使用 `context` 时，才需要 v2 工具来校验。
