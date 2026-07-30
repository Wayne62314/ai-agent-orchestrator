# 阶段十第一部分实施报告：自包含 Windows sidecar

## 结论

阶段十第一部分已经把桌面后端从“依赖开发机 Python”推进为可随 Windows
应用分发的自包含 sidecar。它包含项目 Python 代码、固定版本的
`openai-codex 0.144.4`、匹配的 `codex.exe` 与所需运行库，不读取或写入系统
Python 环境，也不创建云端服务。

本部分暂不生成面向用户的安装程序。Tauri 的 NSIS bundling 仍保持关闭，待第二
部分完成安装身份、开始菜单、快捷方式和卸载行为后再启用。

## 实现内容

### 固定、可重复的构建输入

- 新增 `desktop-build` 可选依赖，固定 PyInstaller 与 Codex SDK 版本；
- 新增明确的 PyInstaller spec，收集动态导入、发行包元数据和官方 Codex
  Windows 运行时；
- 仅支持已批准的 `x86_64-pc-windows-msvc` 首发目标；
- 构建脚本输出 SHA-256、文件大小、Python/PyInstaller/SDK/运行时版本清单。

### 快速启动的自包含目录

最初验证过单文件 PyInstaller 模式，但约 140 MB 的压缩 Codex 运行时导致每次
sidecar 启动都需要长时间临时解压。最终采用目录模式：小型 sidecar 启动器和
只属于它的运行库一起安装，启动无需重复解压。最终 NSIS 安装介质仍会是一个
`Setup.exe`，不会增加用户安装步骤。

本机验证产物：

- sidecar 启动器约 5.3 MB；
- 私有运行库未压缩大小约 437 MB；
- `--self-check` 冷启动约 0.2 秒；
- SDK 与 Codex 运行时均为 `0.144.4`。

这些本机生成文件被忽略，不进入 Git；正式候选产物由受控 Windows 构建生成。

### Tauri 接入

- `bundle.externalBin` 声明目标三元组 sidecar；
- sidecar 私有运行库作为 Tauri resource 放在主程序同级；
- 正式运行优先选择打包后的 sidecar；
- `AIAO_SIDECAR_PATH` 保留为受信任测试覆盖；
- 只有开发环境找不到打包产物时才回退系统 Python。

### 自检和 CI

`--self-check` 只检查应用版本、SDK 元数据和随包 Codex 可执行文件，不建立
数据库、不读取任务、不读取凭据。Windows 原生 CI 现在会：

1. 使用 Python 3.11 安装固定打包依赖；
2. 构建目标三元组 sidecar 和私有运行库；
3. 对冻结后的真实可执行文件运行自检；
4. 再编译并测试 Tauri 原生命令边界。

## 安全与数据边界

- 凭据仍由 Codex 官方登录机制管理，不写入 SQLite 或构建清单；
- 生成清单不包含绝对路径、用户数据或环境变量；
- sidecar 仍只通过私有 stdin/stdout JSONL 与 Tauri 通信；
- 用户数据继续位于 Tauri 应用数据目录，与安装文件分离；
- 没有开放端口、遥测或云资源。

## 设计依据

Tauri 2 要求 `externalBin` 输入以目标三元组为后缀，并在打包时去掉该后缀；
PyInstaller 支持收集动态模块、数据、二进制和发行包元数据。实现遵循：

- <https://v2.tauri.app/develop/sidecar/>
- <https://pyinstaller.org/en/stable/usage.html>
- <https://pyinstaller.org/en/stable/hooks.html>

## 下一部分

阶段十第二部分将启用已批准的 per-user x64 NSIS 安装程序，并实现：

- 稳定的安装身份和覆盖安装；
- 开始菜单入口；
- 可选桌面快捷方式；
- 可选登录后启动，默认关闭；
- 卸载默认保留用户数据。
