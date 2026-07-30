# 阶段十第二部分实施报告：Windows NSIS 安装程序

## 结论

阶段十第二部分已把上一部分的自包含桌面应用接入正式 Windows 安装流程。
Windows CI 会构建单个 per-user x64 NSIS `Setup.exe`，并同时保存 SHA-256
与结构化构建清单。整个流程仍是本地桌面路线，不依赖云端服务、Docker、系统
Python 或管理员权限。

本部分完成的是安装器实现与受控构建。干净 Windows 的安装、覆盖升级、卸载、
重装和恶意软件扫描矩阵属于后续验收，尚不能因为配置测试通过而视为已完成。

## 安装契约

- 目标为 Windows 10 22H2 / Windows 11 的 x64 当前用户安装；
- 程序安装到当前用户的本地应用目录，不请求管理员提权；
- 创建开始菜单入口；
- 交互安装完成页允许用户选择桌面快捷方式；
- 登录后启动单独询问，默认选择“否”；
- 静默安装默认不启用登录后启动，可用 `/AUTOSTART` 明确启用；
- 覆盖升级保留已有的登录后启动选择；
- 禁止用旧版本覆盖新版本；
- WebView2 使用随安装器携带的小型 bootstrapper，仅在需要时下载运行时；
- 卸载器的数据删除选项默认不勾选，因而任务、设置与备份默认保留。

登录后启动使用当前用户的 Windows `Run` 注册表项，不使用管理员级服务。卸载器
会移除该启动项。安装 hook 不读取或删除应用数据目录。

## 构建与产物

桌面版本统一提升到 `0.10.0`，避免安装器把新构建误判为同版本重装。Windows
原生 CI 在 sidecar 自检和 Rust 测试通过后执行 NSIS 构建，然后归档：

- `AI-Agent-Orchestrator-0.10.0-x64-setup.exe`；
- 同名 `.sha256`；
- `AI-Agent-Orchestrator-0.10.0-x64-setup.build.json`。

收集脚本要求构建目录中恰好存在一个安装器，检查其 Windows PE 文件头，再复制、
计算 SHA-256 并记录版本、架构、安装类型、文件大小和源文件名。GitHub Actions
中的安装器产物保留 14 天，缺少任一预期文件都会使 CI 失败。

## 自动验证

新增测试固定以下行为：

- Tauri、前端包和 Rust crate 的桌面版本一致；
- NSIS bundling 已启用且仅为 `currentUser`；
- 禁止降级，WebView2 bootstrapper 与双语资源已配置；
- 登录后启动具有明确的 `/AUTOSTART` 开关且交互默认按钮为“否”；
- installer hook 不包含应用数据删除；
- CI 确实构建、校验并上传安装器。

本地环境缺少 Microsoft C++ linker，因此这里只能完成配置、Python 测试、前端
测试和 Rust 格式检查；完整原生编译与 NSIS 语法验证由 PR 的
`windows-latest` CI 执行。

## 设计依据

- Tauri Windows installer：
  <https://v2.tauri.app/distribute/windows-installer/>
- Tauri 2 NSIS 配置：
  <https://v2.tauri.app/reference/config/#nsisconfig>
- 当前固定 Tauri CLI 的默认 NSIS 模板：
  <https://github.com/tauri-apps/tauri/blob/tauri-v2.11.4/crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi>

## 下一部分

阶段十第三部分应完成升级迁移和真实安装矩阵：

1. 在 Schema 迁移前创建并校验安全备份；
2. 演练 `0.9.x → 0.10.x` 覆盖安装并确认任务、设置、备份不丢失；
3. 演练卸载默认保留数据、显式删除数据和重装恢复；
4. 在干净 Windows 10/11 环境验证首次启动与 Codex 登录；
5. 对候选安装包执行恶意软件扫描和 SHA-256 来源核对。
