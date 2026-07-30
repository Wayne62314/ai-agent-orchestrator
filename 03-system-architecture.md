# 03 — 系统架构

## 1. 架构概览

```mermaid
flowchart LR
    U["用户 / Reviewer"] --> C["控制入口"]
    C --> O["Orchestrator"]
    O <--> S[("状态存储")]
    O --> A["执行引擎适配器"]
    A --> E["Codex 或其他 Agent"]
    E --> W["代码工作区"]
    O --> V["验收执行器"]
    V --> W
    T["定时 / 人工 / 命令事件"] --> B["事件入口"]
    B --> O
    O --> N["通知与审批"]
    N --> U
```

## 2. 组件职责

### 2.1 控制入口

技术 MVP 已提供命令行入口；v1.0 的正式控制入口是 Tauri 2 桌面外壳和
React/TypeScript UI，命令行保留为开发与诊断工具。控制入口负责：

- 创建、查看、暂停、恢复和取消任务；
- 展示等待原因和验证证据；
- 接收审批决定；
- 不直接修改任务状态，所有写操作调用 Python 应用服务并转换为领域事件；
- 不直接访问 SQLite、凭据、Git 或 Codex 子进程。

桌面外壳与 Python sidecar 使用带版本号的 JSONL stdio RPC，默认不监听本地
HTTP 或 WebSocket 端口。详细契约见
[24-stage-7-product-architecture.md](./24-stage-7-product-architecture.md)。

### 2.2 Orchestrator

系统核心，负责：

- 验证事件；
- 根据状态机决定合法转换；
- 调用执行引擎；
- 创建检查点；
- 安排等待条件；
- 触发验收；
- 控制重试和升级。

它不负责写代码，也不解析特定 IDE UI。

### 2.3 状态存储

MVP 推荐 SQLite：

- 单机部署简单；
- 支持事务和唯一约束；
- 比散落的 JSON 文件更适合处理重复事件和并发边界；
- 后续可迁移到服务型数据库。

大型运行日志和产物可作为文件保存，数据库只存路径、摘要和校验值。

### 2.4 执行引擎适配器

对上暴露统一接口：

```text
start(run_request) -> run_handle
inspect(run_handle) -> run_status
cancel(run_handle) -> result
collect(run_handle) -> run_result
```

适配器处理不同执行引擎的启动、输入、状态和结果格式。平台不支持的能力必须显式返回 `UNSUPPORTED`，不能用脆弱的界面模拟冒充稳定接口。

### 2.5 事件入口

把外部信号规范化为统一事件：

```json
{
  "event_id": "evt_...",
  "task_id": "task_...",
  "type": "TIMER_ELAPSED",
  "occurred_at": "ISO-8601",
  "source": "scheduler",
  "payload": {},
  "dedupe_key": "..."
}
```

### 2.6 验收执行器

在限定工作目录内执行预先批准的检查，保存：

- 命令标识；
- 起止时间；
- 退出状态；
- 输出摘要；
- 完整日志位置；
- 是否为必选检查。

### 2.7 通知与审批

MVP 使用本地界面即可。未来可接入邮件、Slack 或 GitHub，但通知渠道不能成为任务状态的唯一来源。

## 3. 核心数据模型

### Task

```text
task_id
title
objective
workspace_path
state
permissions_policy
acceptance_policy
retry_policy
created_at
updated_at
version
```

### Run

```text
run_id
task_id
attempt
engine
state
input_checkpoint_id
started_at
ended_at
exit_reason
result_summary
```

### Checkpoint

```text
checkpoint_id
task_id
run_id
sequence
schema_version
workspace_revision
payload_path
payload_hash
created_at
```

### Event

```text
event_id
task_id
type
source
dedupe_key
payload
occurred_at
processed_at
outcome
```

### Approval

```text
approval_id
task_id
requested_action
risk_summary
status
requested_at
decided_at
decided_by
```

### Verification

```text
verification_id
task_id
run_id
check_name
required
status
exit_code
summary
log_path
```

## 4. 一次恢复的调用链

```mermaid
sequenceDiagram
    participant Event as 事件入口
    participant Orch as Orchestrator
    participant Store as 状态存储
    participant Repo as 工作区
    participant Agent as 执行引擎
    participant Verify as 验收执行器

    Event->>Orch: 恢复事件
    Orch->>Store: 检查事件去重与当前状态
    Store-->>Orch: 最新任务与检查点
    Orch->>Repo: 检查 revision、diff 和关键文件
    Repo-->>Orch: 当前事实
    Orch->>Orch: 构建 Resume Package
    Orch->>Agent: 启动新一轮执行
    Agent-->>Orch: 结果与退出原因
    Orch->>Verify: 执行验收策略
    Verify-->>Orch: 结构化结果
    Orch->>Store: 原子写入结果和新状态
```

## 5. 一致性策略

- 状态转换使用数据库事务；
- Task 使用版本号进行乐观并发控制；
- Event 的 `dedupe_key` 建唯一索引；
- 启动外部动作前写入意图记录，完成后写入结果；
- 无法确认外部动作是否成功时进入 `NEEDS_ATTENTION`，不自动重放高风险动作；
- 检查点内容带 schema 版本和哈希，损坏时拒绝恢复。

## 6. 已选技术形态

v1.0 采用本地桌面外壳加 Python sidecar：

- 桌面外壳：Tauri 2；
- 用户界面：React + TypeScript；
- 后台：现有 Python Orchestrator，以独立 sidecar 运行；
- 私有通信：有版本的 JSONL stdio RPC；
- 数据：SQLite；
- Codex 接入：App Server stdio 和稳定账户/线程接口；
- 任务隔离：每个 Task 一个 Git Worktree，真实 Run 使用独立子进程；
- 日志：结构化 JSON Lines 加人类可读摘要；
- 配置：Local AppData 中的非敏感用户配置；
- 安装：每用户 x64 NSIS `Setup.exe`。

完整桌面架构和用户体验见
[24-stage-7-product-architecture.md](./24-stage-7-product-architecture.md) 与
[25-stage-7-user-experience-spec.md](./25-stage-7-user-experience-spec.md)。
