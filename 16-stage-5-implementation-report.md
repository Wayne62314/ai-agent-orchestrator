# 16 — 阶段 5 可信事件扩展实现报告

## 1. 结论

阶段 5 已完成。编排器现在可以把 CI、PR Review/评论、Issue、服务健康和供应商限流变化注册为持久化等待条件，并且只在来源认证、投递去重、主题一致和白名单条件全部满足时恢复任务。

本阶段没有把“收到 webhook”简单等同于“执行外部指令”。来源认证与内容信任被明确分开：GitHub 的签名可以证明投递来自配置的来源，但 PR 评论正文仍是不可信数据，不能改变目标、权限或审批。

## 2. 数据模型

Schema v5 新增两张表：

- `signal_waits`：保存任务、来源、事件类型、主题、结构化条件、截止时间、超时行为与最终状态。
- `external_events`：保存认证后的脱敏事件收据、去重键、可信级别、消费结果与处理原因。

等待状态为：

```text
ACTIVE → SATISFIED
       → EXPIRED
       → CANCELLED
```

外部事件状态为：

```text
RECEIVED → CONSUMED
         → IGNORED
         → REJECTED
```

去重键固定为 `provider + delivery_id`。同一投递重复到达只读取原结果；如果相同投递 ID 携带不同任务、类型、主题或结构化事实，系统拒绝该冲突。

## 3. 来源认证与内容边界

公网 webhook 使用 HMAC-SHA256：

1. 限制原始请求体最大为 1 MiB。
2. 在 UTF-8 解码和 JSON 解析前进行常量时间签名比较。
3. 密钥只作为运行时字节参数传入，不写数据库、不写日志、不通过 CLI 参数传递。
4. 认证失败只留下带 delivery ID 摘要的审计项，不占用真实去重键，避免伪造请求阻挡合法投递。

本地 App Server 和健康探针通过受信任进程边界注入规范化事件。若将它们部署到另一台主机，必须放到相同的认证入口之后。

PR Review 与评论采用 `UNTRUSTED_CONTENT_OMITTED`：

- 可用于匹配：仓库、PR 编号、事件动作、审阅状态、发送者。
- 不可用于匹配：评论或审阅正文。
- 持久化内容：正文 SHA-256。
- 不持久化内容：正文原文及其中可能出现的凭据、提示注入或恶意指令。

## 4. 五类适配器

| 类型 | 来源 | 主题 | 典型满足条件 | 不可信内容 |
| --- | --- | --- | --- | --- |
| CI 完成 | GitHub webhook | `repo#workflow:<id>` | `status=completed` 且 `conclusion=success` | 无正文 |
| PR Review/评论 | GitHub webhook | `repo#pr:<number>` | 审阅状态、动作、发送者 | 正文省略 |
| Issue 变化 | GitHub webhook | `repo#issue:<number>` | 状态、动作、标签 | Issue 正文不提取 |
| 服务健康 | 本地健康探针 | 服务名 | `status=healthy` | 无 |
| 限流恢复 | 本地 App Server | `provider:bucket` | `available=true` | 无 |

条件不是任意表达式。每种事件只开放预定义字段，值只能是标量或标量列表，避免把外部文本解释为代码。

## 5. 状态转换与超时

注册等待只允许发生在 `RUNNING`：

```text
RUNNING
  └─ SIGNAL_REQUIRED → WAITING_FOR_SIGNAL
       ├─ SIGNAL_RECEIVED → READY
       └─ SIGNAL_TIMEOUT  → NEEDS_ATTENTION
```

截止时间持久化在 SQLite 中，因此进程重启不会丢失等待。超时不会无限轮询或自行放宽条件，而是进入 `NEEDS_ATTENTION` 请求人工判断。

任务状态仍只通过 `OrchestratorService.process_event` 修改。外部事件收据和等待状态分别持久化，并通过稳定的内部事件去重键支持崩溃后的安全重试。

## 6. 命令行

新增：

```text
agent-orchestrator wait register <task_id> ...
agent-orchestrator wait list <task_id>
agent-orchestrator wait expire
agent-orchestrator external-event list <task_id>
```

CLI 刻意不提供把 webhook 密钥放入命令参数的入口。实际 HTTP 接入层应从操作系统密钥存储或部署平台注入密钥，然后调用 `TrustedEventService.ingest_webhook`。

## 7. 验证证据

- 全部 82 项单元与集成测试通过。
- 正确签名的 CI 成功事件可使精确等待进入 `READY`。
- 错误签名在解析载荷前被拒绝，只留下不含原 delivery ID 和载荷的审计项。
- 同一 GitHub delivery 重复投递不会重复状态转换。
- 相同投递 ID 携带不同内容会被拒绝。
- PR Review 正文包含提示注入和令牌样式文本时，原文仍不会进入 SQLite。
- PR Review 条件尝试使用 `body` 字段会被拒绝。
- Issue 关闭、健康恢复和限流可用均完成匹配演练。
- 条件不满足的事件标记为 `IGNORED`，任务保持等待。
- 截止时间到达后，等待标记 `EXPIRED`，任务进入 `NEEDS_ATTENTION`。
- 重开 SQLite 连接后，活动等待仍可查询。
- 状态转换完成但事件收据尚未最终落库的模拟崩溃可通过重复投递收敛为 `CONSUMED`。
- 阶段 5 新增模块通过 Ruff 检查。
- `python -m build` 成功生成 0.5.0 sdist 与 wheel；隔离虚拟环境安装后的导入版本为 `0.5.0`。

## 8. 已知边界与下一步

- 当前提供领域服务和适配器，尚未内置常驻 HTTP webhook 服务器。
- GitHub App 安装、组织级权限、事件订阅配置和密钥轮换属于部署层。
- 本地受信任事件依赖进程边界；跨主机时必须增加认证传输。
- 当前一个精确主题只允许一个活动等待，避免同一信号被多个条件竞争消费。
- 下一步应建立真实 GitHub webhook 端到端演示，并把部署写操作继续接入阶段 4 的审批与副作用账本。
