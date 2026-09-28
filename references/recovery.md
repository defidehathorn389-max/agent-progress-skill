# 冲突、恢复与一致性

## 并发与分叉

- 同一项目的写入依靠四层保护：独占锁 `.write.lock`、不可变检查点、原子替换 HEAD、`--expected` 比较并交换。不同项目可以并行推进。
- `--expected` 必须填**刚读到的**、**目标库自己的** HEAD。它是防覆盖保护，不是随手填个最新 ID 应付检查。曾出现过误用另一个库 HEAD 触发并发断言的情况：断言失败时，先核对目标库的 ref 和父提交，再区分是真并发还是传错了 expected。
- 本地锁无法替代跨机器协调。跨会话并发由 Git 处理：`progress_sync.py push` 会先 fetch，再把本地提交 rebase 到远端之上。
  - 冲突只落在派生视图（`INDEX.md`、`CURRENT.md`）时，工具会根据合并后的 HEAD 自动重新生成视图并继续。
  - 冲突涉及 `HEAD.json`、检查点或其他文件时，工具会中止 rebase，保留本地提交，并报告冲突路径。**绝不强推。**
- 同一项目真正分叉（两个会话各自写了新检查点）时，`push` 会拒绝并提示 `reconcile`：
  1. `progress_sync.py reconcile --project P`：把对方的检查点文件（不可变，已校验）复制到本地，不改 `HEAD.json`；输出双方的检查点、共同祖先，以及各字段的差异（`only_theirs` / `only_ours`）。
  2. 分别核对两边，合并用户意图与实际进度：从 `handoff.py state` 导出己方状态，补上只有对方有的内容，写到 `/tmp/merged.json`。
  3. `handoff.py checkpoint --project P --expected <己方HEAD> --merge-parent <对方HEAD> --state /tmp/merged.json --note '合并原因及证据'`，生成双亲检查点。
  4. `progress_sync.py push --project P`：rebase 无法重放时，工具自动改用 merge；`HEAD.json` 只会被解析为能追溯到另一方的那个检查点（即合并检查点），派生视图重新生成，合并后的链校验通过才提交。
  不要随便选一边，也不要假造完成状态，绝不强推。

## 崩溃与残留

- HEAD 已更新但 CURRENT/INDEX 过时：运行 `rebuild` 重建视图，不回滚 HEAD。
- 检查点已写入但 HEAD 未更新，会形成孤立检查点（`validate` 会列出）。先对照产物证据检查内容，再决定是否接续；工具不会自动把它提升为当前状态。
- 锁残留：先看锁文件里的 PID/时间和实际任务状态，无法确认时问用户。工具不自动抢锁。
- 批处理要失败即停（`set -euo pipefail`），并检查 checkpoint 命令的返回值以及新的 HEAD/revision。检查点失败时，不得继续基于旧 HEAD 声称“本轮新状态已完成”；已上传的其他文件要准确限定范围，纠正后再写并核验真正的新检查点。

## save 的提交范围与根目录改动

- 保存前先查看 `git status --short`。项目保存默认只包含该项目和派生视图；根目录的 `PRIVACY_TERMS.json`、`ALIASES.json` 或 `LOCATION.json` 等相关变更，需要逐个用 `--path` 显式纳入：
  `progress_sync.py --token-file <仓库外凭据文件> save --project <方向/ID> --expected <HEAD> --patch <补丁文件> --note '实际变化' --path PRIVACY_TERMS.json`。
- 若 `save` 报告“检查点已写入，但因所选路径外存在未提交变更而 LOCAL_ONLY”，先读取项目 HEAD，确认已生成的检查点；修复提交范围后运行 `push --project <方向/ID> --path <相关文件> -m '实际变化'`，推送同一个检查点，不重复执行旧补丁，不用 `--path .` 夹带无关项目，更不能强推。
- 只有修复后的命令返回 `PUSHED_VERIFIED` 才报告同步完成；首次失败属于本地保存，不是已推送。

## 稀疏恢复（只取回部分文件）

`validate`、`read`、`checkpoint` 会迭代校验全部可达父链（v2 没有递归深度限制）。正常 `git clone` 本来就包含完整父链。

如果只通过 API 取回了 HEAD 和当前检查点：
- `resume` 和 `read --allow-missing-parents` 仍可读取，但会明确报告“本地缺 N 个祖先”；
- `validate` 和写入仍要求完整父链。应按**同一个不可变远端提交**补齐父链（含合并双亲）并逐个核对 SHA256；
- 缺父文件应记录为“本地恢复不完整”，不能据此认定远端损坏、旧工作丢失，也不能绕过链校验。不要为了补父链去加载无关项目；分支上的非可达检查点保留原样，不自动提升为 HEAD。

## 语义一致性门禁

散列校验成功只证明内容完整，不证明各字段互相一致。接手和追加检查点时：

- 交叉核对 title、goal、pending、next_actions、`handoff.first_action`、`handoff.current_request_type` 与最新有来源的 decisions 和业务清单；
- 旧任务完成后，不得沿用它的“启动中”状态、未确认的选择、会话工作路径或旧 voice_id 作为当前入口；
- 冲突要带证据在新检查点里纠正；证据不足才向用户确认。不能凭旧摘要重做已完成的工作。历史保持不可变，CURRENT/INDEX 由新 HEAD 派生。

## 远端回读不一致

推送后如果立即回读发现不一致，先把状态记为“不确定”，不要盲目重推或强推：

1. 重新读取远端 ref（`git ls-remote`，或者绕过缓存调用 API）；
2. 核对它的父提交与预期基线；
3. 按不可变提交重新取回目标文件并校验指纹；
4. 确认提交与内容一致才记为已同步；如果确实是他人并发写入，就读取后合并。不要把缓存或传播延迟的猜测当成确定的根因。

## 信任边界

哈希是内容一致性检查，不是身份签名，也证明不了语义正确或用户已批准。能写仓库的人可以重写整条记录，所以要结合可信来源和仓库权限来判断。
