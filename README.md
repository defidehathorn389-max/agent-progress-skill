# Agent Progress：唯一进度与交接入口（v2）

让接手的模型读取文件，而不是猜测上一段聊天。项目按“方向/项目”隔离，包含不可变的历史检查点、证据、纠错记录、下一步和同步边界。

- 入口：[SKILL.md](SKILL.md)（接手 → 记录 → 同步 → 收尾）
- 凭据与自动同步：[SESSION_POLICY.md](SESSION_POLICY.md)
- 新会话模板：[REMOTE_START.md](REMOTE_START.md)
- 细节：[references/](references/)（恢复与冲突、同步原理、字段与补丁规范、便携包）
- 变更说明：[CHANGELOG.md](CHANGELOG.md)

## 交给下一位模型的一句话

> 请先读取 agent-progress-skill 的 SKILL.md，按其“快速上手”克隆私有进度库，用 `handoff.py resume --project 【方向/项目ID】` 接手；先说明已完成、未完成和下一步再继续。工作中和结束前用 `update` 写检查点，并用 `progress_sync.py push` 推送，拿到 PUSHED_VERIFIED 再说已同步。无法访问时，先告诉我缺什么。

## 文件分工

- 本仓库（公开）：通用规则、工具、模板和测试，不含任何真实进度或秘密。
- `agent-progress`（私有）：各项目的真实状态，路径为 `projects/<方向>/<项目ID>/`。
- 素材/工程仓库：大媒体和工程文件。进度里只引用它们的不可变版本和哈希。
- 便携包：离线时使用的副本，含私有进度，不得公开。

## 命令

```bash
S=scripts
python3 $S/handoff.py list
python3 $S/handoff.py resume --project research/example          # 接手简报
python3 $S/handoff.py read   --project research/example          # 完整 JSON
python3 $S/handoff.py state  --project research/example > /tmp/state.json
python3 $S/handoff.py checkpoint --project research/example --expected NEW --state templates/state.json --note '创建项目'
python3 $S/handoff.py update --project research/example --expected <HEAD> --patch templates/patch.example.json --note '…' --dry-run
python3 $S/handoff.py validate [--deep] [--verify-local --workspace DIR]
python3 $S/handoff.py rebuild
python3 $S/handoff.py new    --project research/example --title '示例' --goal '…' --next-action '…'
python3 $S/progress_sync.py --token-file /tmp/.gh_token clone --repo OWNER/agent-progress   # 可重复运行
python3 $S/progress_sync.py --token-file /tmp/.gh_token doctor
python3 $S/progress_sync.py --token-file /tmp/.gh_token pull
python3 $S/progress_sync.py --token-file /tmp/.gh_token save --project research/example --expected <HEAD> --patch /tmp/patch.json --note '…'
python3 $S/progress_sync.py --token-file /tmp/.gh_token status --fetch
python3 $S/progress_sync.py --token-file /tmp/.gh_token push --project research/example -m 'research/example: …'
python3 -m unittest discover -s tests
```

`handoff.py` 和 `progress_sync.py` 只依赖 Python 3.9+ 标准库和 git 命令行。工具能检查结构、引用、哈希、陈旧写入、部分秘密格式和同步状态，但不能证明模型遵守了规则，也不能代替人工验收。

## 工具一览

| 文件 | 作用 |
|---|---|
| `scripts/handoff.py` | 本地检查点：锁、expected-HEAD、不可变快照、父链校验（迭代实现，无深度上限）、补丁更新、接手简报、lint、派生视图。不联网 |
| `scripts/progress_sync.py` | Git 同步：clone/doctor/pull/status/push/save；token 只在内存中使用；定向暂存；派生视图冲突自动重建；用 ls-remote 核验；快照丢失 `.git/config` 时自动修复 |
| `scripts/export_handoff.py` | 离线便携包（排除凭据和媒体，附清单） |
| `scripts/credential_vault.py`、`session_github.py`、`sync_progress.py` | 可选的密文封装模式（旧流程，需要 `requests cryptography`） |
| `templates/state.json`、`templates/patch.example.json` | 新项目模板、补丁示例 |

## 远程入口

- 公开规则：https://github.com/defidehathorn389-max/agent-progress-skill
- 私有进度：https://github.com/defidehathorn389-max/agent-progress

仓库的实际部署状态以私有进度的 `LOCATION.json` 为准；同步状态以 `progress_sync.py status --fetch` 为准。平台支持安装 Skill 时，可以把本仓库注册为 `agent-progress`；仅有 GitHub 地址不会自动提供斜杠命令。
