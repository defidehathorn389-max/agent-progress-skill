# 变更记录

## v2.3.3 — 2026-09-27

- 按用户要求：每次回复都以选项收尾（选择题/自定义回答组件；没有组件时用编号选项加“其他”），不结束对话。已同步到 REMOTE_START.md 的提示词和 SKILL.md §5。

## v2.3.2 — 2026-09-27

- REMOTE_START.md 的新会话提示词改用当前流程：doctor → find/recent → resume → save（PUSHED_VERIFIED）→ 用提问组件收尾；写明 token 的存放规则。

## v2.3.1 — 2026-09-27

- 项目别名：根目录可选的 `ALIASES.json`（`{"projects": {"domain/id": ["别名", …]}}`，改了不需要写检查点），加上 `context.aliases`。`find` 命中项目自己的别名时权重最高，查询包含别名也算命中（例如“上次那个谜题动画的第25期”）。在真实数据上，此前有 4 个查询排错或找不到，现在都指向正确的项目。
- `find` 也搜索 `sync` 字段（仓库名）；`doctor` 把 ALIASES.json 里不存在的项目作为提示报告。
- 离线打包：显式跳过 `.secrets/`；超过 2 MiB 的文本文件改为排除并报告（`oversized_text_excluded`），不再中止整个导出。已在真实工作区上验证：解包后逐文件哈希一致，v2 的 validate/resume/verify-local 可以直接使用。
- 新增 4 项测试（合计 69 项）。

## v2.3.0 — 2026-09-27

- `handoff.py find 关键词`：用户没给项目 ID（“上次那个城市纪录片”）时定位项目。搜索所有项目的标题、目标、context（含 aliases）、决定、约束、待办和近期记录；多个词须同时匹配；按相关度排序，同分时最近更新的在前。
- `handoff.py recent`：所有项目最近的检查点时间线（“上个会话做了什么”）。
- `resume` 显示 context 摘要（键名；嵌套对象列出子键），`compact` 移进 context 的领域细节因此仍看得到；已精简的项目会注明父检查点。
- INDEX 顶部新增“进行中”区块（未完成的项目及其等待事项），下面仍是按方向排列的完整列表。
- 新增 4 项测试（合计 65 项）；修正了测试发现的同分排序问题（原先较旧的项目排在前面）。

## v2.2.0 — 2026-09-27

- **同一项目在两个会话中分叉时，可以恢复了**：以前 `push` 总是 rebase，第一个本地提交就会在 `HEAD.json` 上冲突，即使已经写好了双亲合并检查点也推不上去，文档里的恢复流程实际走不通。现在：`reconcile --project P` 复制对方的检查点（校验后，不改 HEAD），列出双方状态、共同祖先和逐字段差异；写好 `--merge-parent` 检查点后，`push`/`pull` 在 rebase 无法重放时自动改用 merge，`HEAD.json` 只会解析为能追溯到另一方的检查点，合并后的链校验通过才提交。
- `handoff.py compact`：先预览、再执行的状态精简（领域细节不丢失地移到 context；修剪旧的 completed、被取代的决定、未引用的证据和 `sync.memory`；可选 `--externalize-artifacts`，把产物列表放进 SHA256 固定的清单文件）；写入 `context.compacted_from`。在一个真实的 51 KB 状态上预演，缩小到 31.6 KB，提示全部消除。
- `handoff.py log`：检查点时间线；`resume` 增加“之前的记录”（最近 5 次，合并检查点有标注）。
- INDEX 为未完成的项目显示 ⏳ 等待事项（`handoff.waiting_for`）。
- 新增 4 项端到端测试（合计 61 项），包括 分叉 → reconcile → 合并检查点 → push → 另一端 pull 并校验。

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
