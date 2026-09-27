# 新会话启动模板（不含任何真实秘密）

把下面的模板作为新会话的第一条消息。token 只在发给可信模型的**私有消息**里填写，不保存到 Skill、进度库或任何文件。

## A. 明文 token 模式（推荐）

```
本次任务：【要做的事；没有新任务就写“列出进行中的项目让我选”】
项目：【方向/项目ID，或你平时的叫法；不确定可以不写】

请按 https://github.com/defidehathorn389-max/agent-progress-skill 的 SKILL.md 操作：
1. 按“快速上手”准备工具和私有进度库 https://github.com/defidehathorn389-max/agent-progress，先运行 doctor；
2. 我没给项目 ID 时，用 find / recent 定位项目，再用 resume 接手；动手前先告诉我：已完成什么、还剩什么、下一步做什么；
3. 工作中和结束前用 save 写检查点（交接文档 = 该项目的 CURRENT.md），看到 PUSHED_VERIFIED 才算同步；
4. 每次回复都以选项收尾，不要结束对话：用客户端的选择题/自定义回答组件列出下一步选项让我选择，并留出我补充的位置；没有这个组件时，用编号选项加一个“其他（自己写）”。

GitHub token（本对话内复用；可以存到仓库之外的 /home/user/.secrets/github_token，不要提交进仓库，也不要写进远端 URL）：
【粘贴 token】
```

## B. 密文封装模式（可选）

把上面模板末尾的 token 两行换成：

```
本次会话解密口令：【在私有消息中填入】
密文封装 JSON 的 Base64：【在私有消息中填入，或附上独立的密文文件】
（用公开仓库 scripts/credential_vault.py 的 unlock 在内存中解密，不回显 token。）
```

## 注意

- **凭据和说明放在同一条消息里，等于把完整访问能力交给接收者。** 只发给可信的接收方，不要公开、入库，也不要让模型把整条消息写进长期记忆。
- 推荐使用只授权 `agent-progress`（需要时再加素材库）、权限为 Contents 读写、有过期时间的 fine-grained token。不要使用带管理或删除权限的 classic token。
- 当前没有新任务时，可以写“列出待办供我选择”。
