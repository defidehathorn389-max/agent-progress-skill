# 沙箱存储：常驻区 + 工作区

Arena 这类沙箱在**轮次之间**只保留工作区快照（约 128 MB 或 1 万个文件；`build`、`dist`、`.cache`、`node_modules` 等目录名和 `.git/config` 永远不保留），而且两轮之间经常重启。**同一轮里**磁盘有约 20 GB 可用（`/tmp` 是约 1 GB、占内存的临时盘）。超过快照上限时只是“尽力保存”，恢复出来的仓库可能不完整，所以要主动控制常驻区的体积。工具：`scripts/workspace.py`（标准库 + git）。

## 两层

| 区域 | 位置 | 放什么 | 下一轮还在吗 |
|---|---|---|---|
| 常驻区 | `/home/user` | 三个私有库的轻量克隆、规则库、`.secrets/` 里的 token；目标 ≤ 60 MB、≤ 5000 个文件 | 在 |
| 工作区 | `/home/user/.cache/work/<名称>` | 本轮要用的素材、渲染中间文件、成片 | 不在（重启后从 GitHub 重新取） |

GitHub 才是真正存东西的地方：进度（`save`）、记忆（`memory.py sync`）、skill（`skills.py publish`）、项目文件（项目仓库或 Release 附件，单个文件 < 2 GiB）。

## 命令

- `workspace.py status`：按快照规则统计常驻区的大小和文件数，列出最大的目录并给出建议；`doctor` 里的 `workspace` 检查用到 70% 提醒、85% 报失败。
- `workspace.py slim`：把 agent-progress 换成轻量克隆（只取历史的目录结构、不取旧文件内容；工作树不含 `evidence/` 媒体和旧的 `sync-receipts/`）。确认新克隆与远端是同一提交、并且 `validate` 通过之后才替换；有未推送的改动时拒绝执行。需要某个证据文件时：`git -C /home/user/agent-progress sparse-checkout add /evidence/<路径>`。
- 新环境直接轻量克隆：`progress_sync.py --root /home/user/agent-progress clone --repo OWNER/agent-progress --slim`。
- `workspace.py open <名称> --repo OWNER/REPO --path episodes/LOGIC-025`：只把需要的目录轻量下载到工作区（再次 `open` 同一名称会补充目录并拉到最新）。
- `workspace.py park`：列出工作区里未提交或未推送的内容（不是仓库的文件夹也会列出，因为重启后就没了）；`park --push -m '…'` 把改动提交推送到各自的仓库并核验（不强推）。

## 规则

1. **常驻区只放小东西。** 下载的素材、渲染出的逐帧图片、分段视频、成片都放工作区，不要放在 `/home/user` 的普通目录里，也不要放 `/tmp`（占内存，只有约 1 GB）。
2. **做完一段就推送。** 回复结束前运行 `workspace.py park`，把需要保留的结果推送到项目仓库或上传为 Release 附件；进度检查点里记下位置（仓库 + 提交 + 路径，或 Release 链接）和 SHA256。
3. **长任务分段、可续做。** 渲染、生成这类可能跨轮的任务按段进行，每完成一段就上传；检查点的 `running_operations` 记下做到哪一段、放在哪里。沙箱重启后，先读检查点，再取回已完成的段继续做，不要从头重来。
4. **逐帧图片不进常驻区**：放工作区，合成视频后删除，避免碰到 1 万个文件的上限。
5. **进度库只放文字。** 推送进度时会拦下新增的音视频文件；图片等其他媒体也应放素材库，进度里只记链接和指纹。
6. 单个项目同时需要超过约 20 GB 时，按集或按目录拆开分批处理。
