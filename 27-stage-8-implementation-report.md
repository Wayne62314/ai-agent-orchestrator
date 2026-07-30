# 阶段八实现报告

日期：2026-07-30

## 1. 结论

阶段八“真实 Codex 执行主链”已经完成。产品内核现在可以从一个
`TaskLifecycleService` 完成仓库选择、隔离 Worktree、真实 Codex Turn、
暂停、恢复、取消、重启恢复、额度等待、自动验收、有限修复和最终状态收敛。

本阶段仍然是后端产品能力，不包含普通用户桌面界面。阶段九将把这些稳定
DTO 和应用服务接入 Tauri 2 + React/TypeScript UI。

## 2. 主要交付

### 2.1 产品级任务生命周期

- `TaskLifecycleService.create`：检查 Git 仓库，创建 Task 和独立 Worktree；
- `start`：取得单活动任务租约并启动真实或测试执行引擎；
- `pause`：先中断 Turn、原子写入 Checkpoint，再进入 `PAUSED`；
- `resume`：校验 Checkpoint 哈希和工作区漂移，沿用原 Codex thread；
- `collect`：完成 Run，执行验收并在预算内自动发起修复 Turn；
- `cancel`：在终止前保存恢复证据，保留 Worktree；
- `recover_expired` / `recover`：把进程退出后的过期 Run 转成可恢复任务；
- `continue_ready`：在可信外部信号解除等待后从 Checkpoint 继续。

所有 Task 状态变化仍只通过 `OrchestratorService.process_event` 完成。

### 2.2 Codex App Server

- 使用本地 stdio JSON-RPC，不监听本地或公网端口；
- 使用稳定的 `account/read`、`account/login/start`、`thread/start`、
  `thread/resume`、`turn/start`、`turn/interrupt` 和 `turn/completed`；
- 支持浏览器登录、设备码登录、API Key 登录、退出和账户状态读取；
- 登录凭据继续由 Codex 管理，Orchestrator 不持久化密钥；
- Turn 默认 `approvalPolicy: "never"`，所有高风险授权仍由 Orchestrator
  的 hash-bound Approval 控制；
- 非预期 App Server 审批请求会被防御性拒绝；
- read-only 和 workspace-write 都绑定到明确的 Worktree 边界。

### 2.3 Worktree 和并发边界

- 每个 Task 使用 `aiao/task-<id>` 分支和受管 Worktree；
- 原仓库可以保持不变，任务修改被隔离；
- SQLite 持久化仓库、分支、基准 revision、路径和 Worktree 状态；
- v1 同一时间只允许一个非终态 Task 持有执行租约；
- 过期租约不能被另一个任务静默抢占；
- 终态任务默认保留 Worktree；
- 只有终态、干净且获得精确审批的 Worktree 才能删除；
- 删除 Worktree 不删除本地任务分支；
- 脏 Worktree 永不自动删除。

### 2.4 暂停、重启和额度恢复

- Schema v6 增加 `PAUSED`、`task_worktrees` 和 `active_task_lease`；
- 暂停或取消的 Checkpoint 在状态转换之前写入，写入失败会阻止虚假暂停；
- 进程重启后，过期 Run 进入 `NEEDS_ATTENTION` 并生成恢复 Checkpoint；
- 恢复继续使用已持久化的 Codex thread id；
- `UsageLimitExceeded` 和 `RateLimitExceeded` 会生成 Checkpoint 和持久化
  `rate_limit.updated` 等待条件；
- 只有可信的本地 App Server 限额恢复事件才能把任务恢复为 `READY`。

## 3. 数据迁移

SQLite Schema 从 v5 前向迁移到 v6。迁移会重建 Task 状态约束以加入
`PAUSED`，并新增 Worktree 与单活动任务租约表。迁移过程执行外键完整性
检查；已有数据库、重启和重复初始化均由测试覆盖。

## 4. 验收证据

自动化验证覆盖：

- 状态机和 Schema 迁移；
- 真实 Git Worktree 创建、校验、保留和审批后清理；
- 单活动任务互斥和过期租约保护；
- 暂停、Checkpoint、同 thread 恢复和取消；
- 进程重启后的过期 Run 恢复；
- 限额错误、可信恢复信号和继续执行；
- App Server 请求参数、通知、取消和非预期审批拒绝；
- 登录委托和不持久化凭据；
- 自动验收、有限修复、最终报告和审计链。

真实本机端到端验收使用 `openai-codex==0.144.4` 提供的 Codex App Server：

```text
TaskLifecycleService
  -> 创建真实 Git Worktree
  -> 启动真实 Codex read-only Turn
  -> 收集完成结果
  -> 执行必选验收
  -> Task = SUCCEEDED
  -> 审计链校验通过
  -> 精确审批后清理干净 Worktree
```

实测结果：

```json
{
  "task_state": "SUCCEEDED",
  "verification_passed": true,
  "thread_bound": true,
  "worktree_state": "REMOVED",
  "audit_valid": true
}
```

## 5. 安全说明

- 阶段八没有引入云端部署；
- Codex 与 Orchestrator 之间仅使用本机 stdio；
- 默认沙箱仍是 read-only；
- workspace-write 必须由调用方显式选择；
- 网络访问默认关闭；
- 验收命令继续以参数数组和 `shell=False` 执行；
- 原始 stdout/stderr 不写入 SQLite；
- 凭据不进入 Task、Event、Checkpoint、审计或测试夹具。

## 6. 阶段九入口

阶段九可以直接围绕以下稳定能力构建 UI：

- Codex 登录状态和登录流程；
- 项目选择与仓库检查；
- Task 创建和权限配置；
- 开始、暂停、恢复、取消和重启恢复；
- 状态、Checkpoint、验收证据和最终报告；
- Approval 和 Worktree 清理；
- 后台健康状态与本地通知。

UI 不直接访问 SQLite，也不直接调用 Codex；它只通过阶段七定义的私有
RPC 调用阶段八应用服务。
