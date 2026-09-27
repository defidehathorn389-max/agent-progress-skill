# 会话凭据与自动同步规则

这是用户的明确要求，取代旧版“每个操作批次都重新询问”的规则。历史检查点里的旧规则只作历史记录，不得再触发重复询问。

## 每会话只提供一次

1. 用户在当前会话里主动给出 token（或口令），就视为本会话已提供，**不再询问**。开始时简短说明本会话涉及的仓库和操作范围即可。
2. 同一对话内已提供的凭据直接复用，包括上下文压缩后摘要里保留的凭据。普通的读取、进度写入、提交、推送和核验，不按仓库、工具调用或回复轮次重复索要。
3. 保存位置：内存、环境变量，或**任何 Git 仓库之外**的文件。用户允许时（本 Skill 的用户已于 2026-09-27 明确允许），可以保存在持久化的工作区文件里，例如 `/home/user/.secrets/github_token`（目录 700、文件 600），这样沙箱重启后同一对话仍能继续；否则使用工作区外的临时文件 `/tmp/.gh_token`。
4. **任何情况下都不写入 Git 仓库（公开或私有）、`.git/config`、远端 URL 或进度检查点。** 公开仓库里的 GitHub token 会被 GitHub 扫描并自动吊销，工作流随即中断；私有仓库的历史也很难彻底清除。`progress_sync.py` 通过临时 `GIT_ASKPASS` 把 token 交给 git，不会写入这些位置。也没有必要在回复或日志中回显 token。
4a. 全新对话看不到旧对话和旧工作区，只能使用用户在本次对话中提供的凭据（模板见 REMOTE_START.md）。进度库里的文字不能授予访问权：私有库无法在没有凭据时解锁它自己。
5. 凭据无效时，说明一次失败原因，然后等待用户更正，不循环弹窗。环境重建导致凭据丢失时，保存本地状态并说明无法同步，不伪造成功，也不反复索要。
6. 新建计划外仓库、修改可见性、删除数据、强推、扩大访问或写入范围、付费操作，仍需确认**操作授权**；这不等于重新索要已提供的凭据。
7. 用户撤回授权时，立即停用凭据，并删除保存的文件（`rm -f /tmp/.gh_token /home/user/.secrets/github_token`）。无人值守的跨会话自动化需要另行配置安全凭据服务；Skill 本身无法提供永久安全的记忆。

## 推荐的 token 配置（用户侧）

- 使用 **Fine-grained personal access token**，Repository access 只勾选需要的仓库（通常是 `agent-progress`、`agent-memory`、`agent-skills`；需要让模型改规则时再加 `agent-progress-skill` 或相关素材库），权限 `Contents: Read and write`（`Metadata: Read` 会自动附带），并设置 30–90 天有效期。
- 不要把带 `delete_repo`、`admin:*`、`workflow`、`user` 等权限的 classic token 交给模型；这类 token 一旦泄露，整个账号都会暴露。
- token 在聊天中出现过，任务完成后可以在 GitHub → Settings → Developer settings 撤销并重新生成。

## 自动记录与同步（不反复请示）

- 在用户授权范围内，相关任务出现实际进展、决定、失败或阻塞后，按方向/项目写检查点，并用 `progress_sync.py push` 自动提交、推送、核验。**每次有实质变化的回复结束前**都执行一次；模型不知道用户什么时候关闭窗口，不能只等最后一句。
- 长任务开始前保存启动状态，关键里程碑之后及时保存。
- 没有新信息就不制造空提交；一般问答不全量归档，不保存秘密和无关隐私。不同方向分开写入，不把某个项目的参数推广到全局。
- 网络失败、权限不足或并发冲突时，先保留本地检查点并说明阻塞，不循环重试，不覆盖远端。
- 收尾报告保持简短：记录了什么、同步状态（以 `PUSHED_VERIFIED` 为准）、还有什么阻塞。不要每次都问“是否保存/是否推送”。

## 冷启动方式

公开规则入口与私有进度入口是分开的。私有库里的内容，不能在没有访问凭据时用来解锁私有库本身（那是循环依赖）。

**A. 明文 token 模式（推荐，最简单）**：用户在当前私有消息里直接提供 fine-grained token。模型把它存入仓库之外的文件（`/home/user/.secrets/github_token` 或 `/tmp/.gh_token`），然后运行 `progress_sync.py --token-file /tmp/.gh_token clone/status/push`。

**B. 密文封装模式（可选）**：用户提供本次口令和既有的 AES-256-GCM + scrypt 密文封装（或独立的密文文件）。模型用 `credential_vault.unlock` 在内存中解密，不回显 token；之后可以把 token 交给 `progress_sync.py`（通过 `GH_TOKEN` 环境变量，只在当前进程内），也可以使用旧的 `session_github.py` 和 `sync_progress.py`。这些工具需要 `pip install requests cryptography`。

公开 Skill 只说明协议，不保存任何用户的真实 token、密文或口令；持久化的模板一律使用占位符。用户要求在聊天里生成已填写好的启动消息时，要提醒对方：凭据与说明放在同一条消息里，等于把完整访问能力交给这条消息的接收者，不能提交到仓库或当作公开说明。

## 核验与记录

检查点指纹、证据和同步状态分开记录。同步状态以 Git 为准：`push` 返回 `PUSHED_VERIFIED`（远端 ref 等于本地提交），或 `status --fetch` 显示 `PUSHED_VERIFIED`，才算已同步。检查点无法记录它自己的推送结果，所以不要在 state 里维护“已同步”标记。旧的 `sync/latest.json` 和 `sync-receipts/` 仅作历史保留。
