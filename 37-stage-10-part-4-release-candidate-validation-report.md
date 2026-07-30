# 阶段十第四部分实施报告：候选安装包验证

## 结论

阶段十第四部分把候选安装包从“能构建、能静默安装”推进到可重复的首次启动、
真实跨版本升级和恶意软件扫描门禁。验证不依赖会过期的历史 Actions artifact：
每一轮 CI 都从已批准的 `0.10.0` 合并提交重建基线安装器，再与当前 `0.11.0`
候选包完成端到端覆盖升级。

这仍是本地桌面产品路线。GitHub Actions 只承担构建和一次性验证，不托管应用、
用户数据库、Codex 会话或运行中的服务。

## 候选首次启动

候选安装包在全新的 `windows-2022` 和 `windows-2025` hosted VM 上分别执行：

1. 当前用户静默安装；
2. 验证主程序、冻结 sidecar、卸载器和快捷方式；
3. 运行冻结 sidecar 自检；
4. 启动真实 Tauri 主程序；
5. 等待应用通过私有 IPC 创建真实 SQLite 数据库；
6. 关闭进程并等待 sidecar 退出；
7. 默认卸载并确认 Roaming/Local AppData 数据仍存在；
8. 再次安装验证 `/AUTOSTART`，卸载后确认启动项移除。

候选验证 job 不调用开发态 Python 入口，但 runner 镜像本身仍包含开发工具。因此
该证据证明自包含安装路径和首次启动，不替代最终 Windows 10/11 客户端人工签收。
GitHub 官方说明每个 hosted job 使用新 VM，并列出当前 Windows runner 标签：
<https://docs.github.com/en/actions/reference/runners/github-hosted-runners>。

## 真实 0.10.0 → 0.11.0 覆盖升级

### 可重复基线

CI 使用完整提交
`e27665475cd8d8c3612ab0b453a5ae1992ef2bf2` 检出并重建 0.10.0：

- 固定 Python、Codex SDK、PyInstaller、Node、pnpm、Rust 和 Tauri 输入；
- 构建并自检 0.10.0 冻结 sidecar；
- 生成单个 NSIS 基线安装器、SHA-256 和构建清单；
- 作为同一 workflow 的短期证据传给升级 job。

因此验证不会引用手工上传文件，也不会在 14 天 artifact 过期后失去可重复性。

### 升级场景

升级 job 会：

1. 把 0.10.0 安装到隔离的当前用户目录；
2. 通过 0.10.0 随包 sidecar 创建真实 Git 仓库、Task 和保留 Worktree；
3. 在用户备份目录写入独立保留证据；
4. 将 0.11.0 覆盖安装到同一程序目录；
5. 用 0.11.0 随包 sidecar启动数据库；
6. 验证 Task 仍可查询、Worktree 和用户备份仍存在；
7. 验证 Schema 6→7 只在临时副本完成，并留下带 SHA-256 的升级前备份；
8. 默认卸载 0.11.0 后再次验证数据库、Task Worktree、备份和迁移清单仍存在。

最终生成 `windows-upgrade-evidence.json`，记录两个产品版本、两个 Schema 版本、
任务和数据保留结论、主程序 SHA-256、runner 镜像及 UTC 时间。

## Microsoft Defender 扫描

两个候选验证 VM 都使用系统 Microsoft Defender `MpCmdRun.exe` 对收集后的精确
`Setup.exe` 执行自定义文件扫描：

- 找不到 Defender 扫描器即失败；
- 扫描返回非零即失败；
- 证据绑定安装包文件名、大小和 SHA-256；
- 记录病毒库版本、更新时间、保护状态、runner 镜像和扫描时间；
- 只在无威胁时写入 `result: no-threats`。

每个 runner 的 `windows-defender-scan.json` 独立上传。该扫描是候选门禁，但不
等同于 Windows 代码签名；代码签名仍按已批准路线延期到 v1.1 或首次公开发布前。

## 强制门禁

总 CI 现在要求同时通过：

- Python 3.11/3.14 × Windows/Linux；
- 质量、发行包和容器构建；
- 桌面 UI 用户旅程；
- 当前 Windows 原生壳、NSIS 和安装卸载；
- 固定 0.10.0 基线重建；
- Windows 2022/2025 候选首次启动与 Defender；
- 真实 0.10.0→0.11.0 覆盖升级。

任何证据 job 失败都会使总 `CI` 门禁失败，候选安装包不能进入下一阶段。

## 阶段十剩余边界

自动化层面的安装、首次启动、升级、卸载保留、来源校验和 Defender 扫描已经
覆盖。尚未完成的是用户环境签收：

1. Windows 10 22H2 与 Windows 11 客户端各完成一次交互安装；
2. 完成真实 Codex ChatGPT/API Key 登录；
3. 选择一个用户仓库并完成真实任务；
4. 人工确认 200% 缩放、键盘操作、系统通知和卸载界面文字；
5. 记录误报、阻断缺陷和用户反馈。

这些属于阶段十一产品验收，不应继续扩张安装器实现范围。
