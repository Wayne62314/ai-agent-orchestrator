# 阶段十一 PR 4：候选包与黄金旅程报告

> 历史记录：本报告中的用户侧脚本和 JSON 回传方案已撤回。
> 后续用户验收只通过正常应用界面完成。

## 当前结论

PR 4 建立候选包交付和 Windows 11 实机验收闭环。自动验证完成后仍不能宣布产品
可用；最终结论必须等待用户提交完整的 `windows11-acceptance.json`。

## 候选包内容

合并后的 `main` CI 会生成 `ai-agent-orchestrator-windows-installer`，其中包含：

- Windows x64 NSIS 安装程序；
- SHA-256 文件；
- 带完整源码提交、版本和构建来源的构建清单；
- `START-WINDOWS11-ACCEPTANCE.cmd`；
- 纯 PowerShell 黄金旅程记录器；
- 验收记录结构校验器；
- 面向用户的 Windows 11 验收说明。

记录器会在安装前重新计算 SHA-256，并确认安装程序、构建清单和验收记录属于同一
候选提交。所有项目初始为 `not-tested`，脚本会在每一步后保存，失败或受阻必须
填写说明。

## 自动证据

PR CI 必须继续通过：

- Python 3.11 / 3.14，Ubuntu / Windows 测试；
- Ruff、Python 包和容器构建；
- 桌面用户旅程和前端生产构建；
- Windows sidecar、Tauri 和 NSIS 构建；
- Windows 2022 / 2025 安装、首次启动、Defender 与卸载；
- 0.10.0 到 0.11.0 的真实安装包升级。

## 用户证据

用户必须使用合并后 `main` 的同一个安装包完成：

> 安装 → 首次启动且无黑框 → Codex 登录 → 账户信息真实 →
> 新任务无演示填充 → 真实仓库选择与更换 → 无命令创建 →
> 真实 Codex 任务 → 分层验收报告 → 缩放与键盘 → 通知 → 卸载

在用户返回完整记录前：

- Windows 11 恢复门禁：未完成；
- Windows 10 22H2 验收：未开放；
- 产品可用或可发布结论：不得给出。

## 合并后的操作

1. 等待合并提交的 `main` CI 全绿；
2. 下载该次运行的 `ai-agent-orchestrator-windows-installer`；
3. 在用户电脑解压完整候选包；
4. 双击 `START-WINDOWS11-ACCEPTANCE.cmd`；
5. 用户完成后回传 `windows11-acceptance.json`；
6. 校验证据并决定通过或进入新一轮修复。
