# 变更记录

## v2.1.0 — 2026-09-27

- **抗快照丢失**：有些平台的工作区快照不保存 `.git/config`，恢复后 `origin` 和提交身份都会丢失，fetch/push 随之失败。现在所有同步命令会从 `LOCATION.json` 的 `progress_remote_url`（或 `--remote-url`）自动补回 `origin`，并以最后一次提交的作者作为提交身份。
- `progress_sync.py doctor`：会话开始时一次性检查（凭据、origin、身份、落后/未推送、未提交改动、完整性、派生视图、lint 提示、Skill 版本），每个问题附修复命令；lint 只作提示，不算失败。
- `progress_sync.py pull`：快进，或 rebase 本地未推送的提交（派生视图冲突自动重建）；有未提交改动时拒绝执行，绝不丢失工作。
- `progress_sync.py save`：`update` + `push` 一步完成（回合结束协议）；推送失败时明确报告检查点为 LOCAL_ONLY。
- `progress_sync.py clone` 可重复运行：已有克隆时自动修复并拉取。
- `handoff.py new`：用参数创建项目（title/goal/status/first-action/next-action/related），不必手写 JSON。
- 新增 14 项测试（合计 57 项），包括模拟快照删除 `.git/config` 之后的推送。

## v2.0.1 — 2026-09-27

- 凭据规则按用户决定调整：同一对话中已提供的凭据（包括压缩摘要里保留的）直接复用；用户允许时，可以存放在仓库之外的持久化工作区文件中（`/home/user/.secrets/github_token`）。硬性底线不变：不写入任何 Git 仓库、远端 URL 或检查点（公开仓库中的 GitHub token 会被自动吊销）。全新对话仍需用户提供一次。

## v2.0.0 — 2026-09-27

本版依据真实使用数据修订（只统计汇总数字，不含私有内容）：单个项目 5 天内累积约 290 个检查点；每个检查点通常配一个“回执提交”；约 175 份回执出现了 41 种不同格式；`sync/latest.json` 落后于实际 HEAD 数十个提交；state 内的 `sync.memory` 出现 16 种自造取值，而且大多停在“待同步”。

### 修复
- **父链校验改为迭代实现**。v1 每多一个 revision 就多一层递归，约 1000 个 revision 时 `read`/`validate`/`checkpoint` 会因 `RecursionError` 全部失效，而且这个异常不在捕获列表里，会直接打印 traceback。v2 已用 1500 层链做回归测试。
- 未安装可选依赖（`cryptography`/`requests`）时，测试会跳过密文模式用例，不再导致整个测试套件报错。
- 文件读写统一使用 UTF-8；artifact 路径检查不再依赖当前工作目录；CLI 增加兜底异常处理，不泄露数据。

### 新增
- `handoff.py resume`：接手简报。按接手顺序列出第一步、待办（含阻塞和完成判据）、约束、不要重复的错误、最近决定和运行中任务，附完整性结果和可直接复制的 `update` 命令。真实项目中约 10 KB，而完整 `read` 约 53 KB。
- `handoff.py update --patch`：只写变化（set/unset/remove/upsert/append），支持 `--dry-run` 和 stdin；空变化会被拒绝。
- `handoff.py state`：只导出 state，方便整体改写。
- lint 提示（不阻断写入）：state 过大、handoff 键过多、COMPLETED 仍有待办、待办缺完成判据、已弃用的状态和 `sync.memory`。
- 可选字段 `context`：放领域细节，让 `handoff` 只保留接手必需的键。
- `progress_sync.py`（标准库）：`clone`/`status`/`push`。明文 token 只通过临时 GIT_ASKPASS 在内存中使用，禁用凭据存储助手；只暂存所选项目和 INDEX；拒绝修改检查点、凭据文件、疑似秘密和大文件；rebase 时派生视图冲突自动重建，其他冲突中止并保留本地提交；绝不强推；用 `git ls-remote` 核验。
- `CURRENT.md` 更易读：接手第一步放在最前，列表项按字段渲染，不再是整行 JSON。

### 调整
- **同步证明改由 Git 推导**：不再为每个检查点提交回执，也不再全新克隆两次核验（提交 ID 本身就是整棵目录树的哈希）。旧回执保留为历史。
- 读取时只对 HEAD 做完整内容校验；祖先检查点做结构、指纹和 revision 校验（写入时已校验过内容）。`validate --deep` 会把祖先内容问题作为提示报告，因此以后收紧规则时，不会让不可变的历史“永远不合格”。
- `resume`/`read --allow-missing-parents` 可以在稀疏工作区中读取并提示缺失的祖先；写入和 `validate` 仍要求完整父链。
- 新增疑似秘密格式：Anthropic、OpenAI project、Google API、Slack、GitLab。
- SKILL.md 按工作流重写（快速上手 → 硬规则 → 接手 → 记录 → 收尾），原来追加在文末的事故补丁并入相应章节，细节移到 `references/`。明确“交接文档”就是该项目的检查点和 `CURRENT.md`，不另建 HANDOFF.md。
- SESSION_POLICY/REMOTE_START 增加明文 token 模式（用户实际使用的方式）和推荐的最小权限 fine-grained token。

### 兼容性
- v1 的检查点无需迁移（schema_version 仍为 1），CLI 原有命令和参数不变。首次使用 v2 时运行一次 `rebuild`，重新生成更易读的派生视图。
- 回滚：`git checkout v1-legacy`（打在 v1 最后一个提交上的标签）。
