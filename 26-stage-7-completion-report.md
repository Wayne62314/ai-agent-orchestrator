# 阶段七完成报告

日期：2026-07-30

## 1. 结论

阶段七“产品定义与桌面架构”已完成并通过架构评审。项目已经从“命令行内核加
本地运行脚本”收敛为一个明确的 Windows 桌面产品方案，可以进入阶段八实现真实
Codex 执行主链。

## 2. 已交付

- 产品领域语言和歧义边界：`CONTEXT.md`。
- 桌面技术、进程、安全、凭据、目录、Worktree 和安装架构：
  `24-stage-7-product-architecture.md`。
- 六区域信息架构、七条用户旅程、五个低保真线框、UI 动作映射和 21 项验收场景：
  `25-stage-7-user-experience-spec.md`。
- 七项具有长期影响的架构决定：`docs/adr/0001` 至 `0007`。
- 路线图、未决问题和 README 状态同步。

## 3. 已批准架构

```text
React UI
  → Tauri 2 Windows host
    → private versioned JSONL RPC over stdio
      → Python orchestration sidecar
        → Codex App Server over stdio
```

- Tauri 承担窗口、托盘、单实例、系统通知和 sidecar 生命周期。
- Python 保留领域内核、SQLite、恢复、验收、审批和审计。
- Codex App Server 承担 Codex 登录、凭据刷新、线程和流式 Agent 事件。
- 桌面主链不监听本地或公网端口。
- 程序使用每用户 NSIS `Setup.exe` 安装，用户数据与程序文件分离。

## 4. 现有内核复用

以下能力直接保留：

- `OrchestratorService.process_event` 及状态机；
- SQLite 事务、事件去重和审计哈希链；
- Run 租约和过期恢复；
- Checkpoint 原子写入、哈希验证和 Resume Package；
- Verification Policy、有限修复和 Delivery Report；
- hash-bound Approval 和 Side Effect ledger；
- 敏感信息过滤与受限命令执行。

CLI 继续作为开发和诊断入口，但桌面 UI 不通过 CLI 参数解析器驱动产品流程。

## 5. 阶段八必须实现

1. 增加 `PAUSED` 状态、暂停/恢复事件、向前 Schema 迁移和重启测试。
2. 实现 `TaskLifecycleService`，覆盖启动、暂停、恢复、取消和单活动 Task 互斥。
3. 实现 `CodexSessionService`，通过 App Server 稳定接口完成登录状态和真实 Turn。
4. 实现 `WorktreeService`，持久化 Task 与分支、基准 revision、Worktree 的绑定。
5. 把租约、Checkpoint、额度等待、Verification、修复和报告收敛为后台主循环。
6. 提供阶段九可调用的 Python 应用服务与稳定 DTO；完整桌面 RPC 可在阶段九接入。
7. 在真实仓库完成中断、重启、额度等待、审批和成功交付端到端测试。

## 6. 验证

- 文档中的每个 UI 写动作均映射到现有领域能力或明确的新应用服务。
- UI、Tauri、Python、Codex App Server、SQLite 和 Git 的所有权没有重叠。
- 凭据、Approval、Side Effect、原仓库和升级数据均有明确保护边界。
- 阶段七没有改变运行时代码或 SQLite Schema。
- 仓库单元测试、格式检查和文档差异检查作为 PR 门禁执行。

## 7. 阶段八退出方向

阶段八结束时，必须能够从一个应用服务调用启动真实 Codex Task，在安全点暂停，
关闭并重启后台后恢复，自动完成验收和有限修复，并在整个过程中保持 Worktree
隔离、单活动 Task 互斥、Approval 与审计约束。届时再进入阶段九实现完整桌面 UI。
