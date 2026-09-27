# 新会话启动模板（不含任何真实秘密）

把下面的模板作为新会话的第一条消息。token 只在发给可信模型的**私有消息**里填写，不保存到 Skill、进度库或任何文件。

## A. 明文 token 模式（推荐）

```
本次主题/任务：【……】

请先读取 https://github.com/defidehathorn389-max/agent-progress-skill 的 SKILL.md，并按其规则操作；
私有进度库是 https://github.com/defidehathorn389-max/agent-progress。
对应项目：【方向/项目ID；不确定就列出候选让我选】。

GitHub token（仅本会话使用；只放在内存或工作区外的临时文件，不写入仓库、URL、日志或回复）：
【在私有消息中填入 fine-grained token】

完成任务后：
1. 把本次进度写入该项目的检查点（交接文档 = 该项目的 CURRENT.md），推送后确认 PUSHED_VERIFIED；
2. 不要结束对话：用客户端的选择题/自定义回答组件收尾，在末尾留出我回答和补充信息的位置。
```

## B. 密文封装模式（可选）

把上面模板中的 token 一行换成：

```
本次会话解密口令：【在私有消息中填入】
密文封装 JSON 的 Base64：【在私有消息中填入，或附上独立的密文文件】
（用公开仓库 scripts/credential_vault.py 的 unlock 在内存中解密，不回显 token。）
```

## 注意

- **凭据和说明放在同一条消息里，等于把完整访问能力交给接收者。** 只发给可信的接收方，不要公开、入库，也不要让模型把整条消息写进长期记忆。
- 推荐使用只授权 `agent-progress`（需要时再加素材库）、权限为 Contents 读写、有过期时间的 fine-grained token。不要使用带管理或删除权限的 classic token。
- 当前没有新任务时，可以写“列出待办供我选择”。
