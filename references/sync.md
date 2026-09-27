# 同步与核验

## 四种状态

| 状态 | 含义 | 怎么确认 |
|---|---|---|
| 本地写入 | 检查点和 HEAD 已写到磁盘 | `handoff.py resume` |
| 本地提交 | 已进入本地 Git 提交 | `progress_sync.py status` → `COMMITTED_NOT_PUSHED` |
| 已推送 | push 成功 | push 输出 |
| 远端已核验 | 远端 ref 等于本地提交 | push 输出 `PUSHED_VERIFIED`，或 `status --fetch` 显示 `PUSHED_VERIFIED` |

`status` 的其他状态：`LOCAL_ONLY_UNCOMMITTED`（尚未提交）、`REMOTE_AHEAD`（远端有更新的检查点，写入前先 pull）、`DIVERGED`（两边各有新检查点，见 recovery.md）、`PUSHED_AS_OF_LAST_FETCH`（未重新 fetch，只代表上次获取时的状态）。

## `progress_sync.py push` 做了什么

1. 校验所选项目的完整检查点链；
2. 只重建所选项目的 `CURRENT.md` 和 `INDEX.md`；
3. **只暂存** `projects/<所选项目>/`、`INDEX.md` 和显式的 `--path`，不做 `git add -A` 全仓提交；
4. 拒绝以下情况：修改或删除已有检查点、凭据类文件（`.env`、`*.enc.json`、`credentials/`、`.pem`、`.key`）、疑似明文秘密（含本次 token 原值）、超过 25 MB 的文件（媒体应放素材库）；
5. 提交一次（没有变化就不提交）→ fetch → 必要时 rebase（派生视图冲突会自动重建，其他冲突中止并保留本地提交）→ 普通 push（绝不 `--force`）；
6. 用 `git ls-remote` 读回远端分支，必须等于本地提交 ID，才返回 `PUSHED_VERIFIED`。

## 为什么不再需要“回执提交”和“全新克隆逐文件哈希”

Git 提交 ID 是对整棵目录树的哈希（树 → 子树 → 文件 blob）。远端分支指向与本地相同的提交 ID，就说明远端文件与本地逐字节一致。所以：

- 不再为每个检查点额外提交 `sync-receipts/*.json` 或更新 `sync/latest.json`。旧做法让提交数翻倍，而且回执格式因模型而异（真实使用中出现过几十种格式），`sync/latest.json` 很快就过时；
- 不再为了核验克隆两次整个仓库（进度库有数十 MB 到上百 MB）；
- 任何检查点的同步状态，都可以随时用 `status --fetch` 从 Git 推导出来。检查点记录不了自己的推送结果，这正是旧 `sync.memory` 字段长期停在 `PENDING_SYNC` 的原因。

**旧回执**（`sync/latest.json`、`sync-receipts/`）保留为历史，不再更新，也不要新建。旧的“同步回执必须列出检查点 ID、载荷提交、时间和方法”要求，由 push 的输出和 Git 历史满足：需要时把 push 输出里的 commit 写进下一个检查点的证据即可，不必单独提交。

## 只能用 GitHub API 的环境（没有 git 命令行）

- 写入：用 Git Data API 创建 blob → tree → commit，再更新 ref（`force: false`）。更新 ref 时以你读到的父提交为基线；ref 已被别人移动时，先读后合并。
- 核验：读取 `git/refs/heads/<branch>` 拿到提交，再取提交的 tree（recursive），把每个目标文件的 blob sha 与本地计算值比较。本地计算方式：`sha1(b"blob " + str(len(data)).encode() + b"\0" + data)`。
- Contents API 对较大的文件可能不返回内容体，不要把空内容误判为同步丢失；改用 `/git/blobs/{sha}` 读回。
- 在核对父提交之前，不要重推。

## 会话开始：clone / doctor / pull

- `clone` 可以重复运行：目标目录已有克隆（包括从工作区快照恢复的）时，会自动修复并拉取，不报错。
- `doctor` 一次检查 git、凭据、origin、提交身份、落后/未推送、未提交改动、完整性、派生视图、lint（只作提示）和 Skill 版本（本地 / 远端 / LOCATION 固定版本），每个问题都附修复命令。
- `pull`：远端领先时快进；本地也有未推送提交时 rebase（派生视图冲突自动重建，其他冲突中止并保留本地提交）；有未提交的已跟踪改动时拒绝执行，以免丢失。

## 工作区快照丢失 `.git/config`

有些平台的工作区快照不保存 `.git/config`。恢复后，克隆在本地仍可用（HEAD、提交、`refs/remotes` 都在），但 `origin` 和 `user.name/email` 没了，fetch/push 会失败。所有 `progress_sync.py` 命令都会自动修复：先从 `LOCATION.json` 的 `progress_remote_url`（或 `--remote-url`）补回 `origin`，再以最后一次提交的作者作为提交身份。输出里的 `repairs` 和 `identity` 字段会注明这些修复。含凭据的 URL 会被拒绝。

## 部分工作区与存储上限

- 部分检出时，`INDEX.md` 会保留未下载项目的导航行（`validate` 在 `unloaded_projects` 中列出）。`push` 只提交所选项目，不会镜像删除远端独有的文件。
- 存储紧张时，只有在远端重新读取并校验成功之后，才转移本地冗余副本；同时留下可恢复的仓库、路径、版本和指纹。远端独有的原件不能删除。

## 离线或凭据缺失

保留本地检查点，在回复中明确写 `LOCAL_ONLY` 或 `PENDING_SYNC` 及原因；需要时用 `export_handoff.py` 生成可下载的交接包（见 portable-package.md）。不要声称其他新会话已经能从 GitHub 读到新进度。

## 旧工具（密文封装模式）

`scripts/sync_progress.py` 配合 `session_github.py` 和 `credential_vault.py`，仍可用于“密文 + 口令”的启动方式。它采用旧式的全树提交、两次全新克隆核验和 `sync/latest.json` 回执，速度较慢。日常建议使用 `progress_sync.py`。
