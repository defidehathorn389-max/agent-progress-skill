# 跨模型项目交接 Skill

让接手模型读取文件而不是猜测上一段聊天。按方向/项目隔离，带历史检查点、证据、纠错记录、下一步和同步边界。

入口：[SKILL.md](SKILL.md)。最新授权/自动同步规则：[SESSION_POLICY.md](SESSION_POLICY.md)。无平台专属依赖；本地进度工具只用Python标准库。

## 交给下一模型的话

> 请先读取跨模型交接Skill的SKILL.md，再读取进度库的GLOBAL.md、LOCATION.json和INDEX.md。接手【方向/项目ID】，校验并读取该项目HEAD.json指向的检查点及相关证据；先说明已完成、未完成和下一步，再继续。不要混用其他项目进度，不要重复已完成任务。工作中及结束前更新检查点，并明确本地/远端同步状态。无法访问时先告诉我缺少什么。

使用前将“Skill/进度库”替换为真实可访问的路径或已部署仓库地址。新仓库尚未创建时，不能把计划地址当作已生效入口。

## 文件分工

- 本仓库：通用规则、工具、模板与测试，可以公开。
- 独立进度目录/仓库：具体用户项目状态，默认私有。
- 既有项目仓库：大型媒体和工程，进度只引用其不可变版本与哈希。
- 交接包：离线/断网的便携副本，含私有进度时不得公开；默认不含token密文、口令或原始聊天。

## 命令

```bash
python scripts/handoff.py --root /path/to/handoff checkpoint --project research/example --expected NEW --state templates/state.json --note '创建项目'
python scripts/handoff.py --root /path/to/handoff read --project research/example
python scripts/handoff.py --root /path/to/handoff validate
python scripts/handoff.py --root /path/to/handoff rebuild
python -m unittest discover -s tests -v
```

工具能检测本地结构、引用、哈希、陈旧写入和部分秘密格式；不能证明模型遵守规则、远端最新状态或人工验收。跨机器并发仍需正常Git合并。

## 便携交接包

```bash
python scripts/export_handoff.py --workspace /path/to/workspace \
  --include skills/session-handoff-skill --include handoff \
  --output /path/to/private-handoff.zip
```

只能显式选择需要的规则、进度和文本证据。工具排除凭据目录、密文、常见秘密文件、Git内部文件和大媒体，并生成包内SHA256清单。包里有真实项目进度时必须私有保存。扫描器不是完整的隐私检查。

## 远程入口

公开规则：https://github.com/defidehathorn389-max/session-handoff-skill

私有进度：https://github.com/defidehathorn389-max/agent-progress

仓库的实际部署/核验状态以私有进度的LOCATION.json与sync/latest.json为准。用户在当前启动消息已提供口令时不再询问；每次实质性回复结束前自动保存有变化的进度并推送核验。

## 一次会话授权与自动推送工具

- `SESSION_POLICY.md`：当前会话已经提供口令就不再问；每次有实质性变化的回复结束前自动保存/推送。
- `REMOTE_START.md`：远程可复制模板，只保留占位符，不含真实口令或密文。
- `scripts/credential_vault.py`：AES-GCM+scrypt密文解密，禁止回显明文token。
- `scripts/session_github.py`：内存内复用当前会话认证，受限socket向Git内部管道提供凭据；关闭时清理。
- `scripts/sync_progress.py`：私有进度发布、独立克隆哈希校验和不自引用的检查点同步回执；不自行询问密码。

本地检查点工具仍仅依赖Python标准库。联网认证工具需要`requests cryptography`；只有明确的当前会话授权后才调用。
