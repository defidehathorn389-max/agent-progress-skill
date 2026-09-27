# 离线便携交接包

在断网、没有凭据，或下一个环境无法访问 GitHub 时使用。

```bash
python3 scripts/export_handoff.py --workspace /home/user \
  --include agent-progress-skill --include agent-progress \
  --output /home/user/private-handoff.zip
```

- 只打包显式选择的规则、进度和文本证据。会排除凭据目录（包括 `.secrets/`）、密文、常见秘密文件、Git 内部文件、锁/临时文件和大媒体；超过 2 MiB 的文本文件不打包，但会在结果和清单的 `oversized_text_excluded` 中列出；并在包内生成 `PACKAGE_MANIFEST.json`（逐文件 SHA256）。
- 包里含真实项目进度时，必须私有保存。扫描器不是完整的隐私检查。
- 最终包的指纹记录在包外（回复或下一个检查点的证据里），避免自引用。

## 安全解压与接续

1. 解压前检查每个条目：不得是绝对路径，不得含 `..`，不得是越界符号链接或设备文件；
2. 解压到独立的空目录，不覆盖已有的、可能更新的进度；
3. 对照包内清单逐一核对 SHA256；
4. 运行 `validate`，逐个项目 `resume`，核对检查点与预期版本；
5. 新目录里的媒体还没恢复时，不要急于宣称 `--verify-local` 通过；
6. 已经有进度时，比较两边的检查点后再合并（见 recovery.md），而不是直接解压覆盖。
