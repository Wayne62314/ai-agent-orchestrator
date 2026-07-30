# 10 — 执行引擎接入决策

## ADR-005：Codex 接入架构

- 状态：已接受
- 日期：2026-07-30
- 决策范围：MVP 的首个执行引擎

## 背景

Orchestrator 需要：

- 启动一次 Codex 运行；
- 传入 cwd、权限和恢复上下文；
- 观察完成、失败和中断；
- 在新进程中恢复 thread；
- 主动中断；
- 读取 ChatGPT 管理的 Codex 限额窗口；
- 避免依赖桌面 UI 自动化。

## 候选方案

### A. `codex exec`

优点：

- 最简单；
- JSONL 和输出 Schema 适合批处理；
- 支持 session resume；
- 容易在 CI 使用。

不足：

- 进程级控制，不如 App Server 适合长连接事件监听；
- 额度更新不是其主要接口；
- 多轮控制需要反复启动进程。

判断：保留为批处理和故障降级入口，不作为主控制平面。

### B. Python Codex SDK

优点：

- 官方高层 API；
- 自带固定版本 Codex CLI runtime；
- 本机 Windows 实验成功；
- 支持 thread start、resume、run 和 interrupt；
- 原生适合 Orchestrator 的 Python 实现。

不足：

- 当前高层 API 没有直接暴露全部 App Server 账户方法；
- Python SDK 当前仍处于 beta 发布阶段。

判断：作为任务执行主接口。

### C. 直接使用 App Server JSON-RPC

优点：

- 能力最完整；
- 有细粒度生命周期事件；
- 能直接读取限额并监听更新；
- 可生成与特定版本匹配的 Schema。

不足：

- 接口面较大；
- CLI 将 App Server 标记为 experimental；
- 客户端必须正确处理握手、通知、server request、去重和版本变化。

判断：仅实现满足限额观察与原始事件审计的薄层，不在 MVP 中封装全部方法。

### D. 桌面 UI 自动化

优点：

- 理论上能复用用户已登录界面。

不足：

- 脆弱；
- 难以可靠识别状态；
- 无结构化事件；
- 不应作为无人值守系统的控制平面。

判断：拒绝。

## 决策

采用组合方案：

1. Python Codex SDK 负责 thread 生命周期和执行；
2. App Server stdio JSON-RPC 薄适配层负责：
   - `account/read`
   - `account/rateLimits/read`
   - `account/rateLimits/updated`
   - 必要的原始事件审计
3. `codex exec --json` 作为一次性批处理和适配器故障时的后备；
4. Codex 桌面 heartbeat 只作为可选唤醒渠道；
5. MVP 不使用远程 WebSocket。

## 适配器接口

```text
ExecutionAdapter
  start(task, checkpoint) -> RunHandle
  observe(run_handle) -> EventStream
  interrupt(run_handle) -> InterruptResult
  collect(run_handle) -> RunResult
  resume(thread_id, checkpoint) -> RunHandle

RateLimitProvider
  read() -> RateLimitSnapshot
  watch() -> EventStream

WakeupProvider
  schedule(task_id, not_before) -> WakeupHandle
  cancel(wakeup_handle) -> CancelResult
```

分开 `ExecutionAdapter` 与 `RateLimitProvider`，避免认证类型或额度接口变化迫使整个执行适配器重写。

## 版本策略

- 初始验证版本：`openai-codex==0.144.4`；
- 锁定 SDK 与其自带 runtime；
- 不直接引用 WindowsApps 桌面程序路径；
- 安装后记录 SDK 和 CLI 版本；
- 用 `codex app-server generate-json-schema` 保存协议 Schema；
- 升级时运行：
  - 握手测试；
  - account/read 测试；
  - rateLimits/read 测试；
  - start/run/resume 测试；
  - interrupt 测试；
  - 错误分类测试。

`0.144.4` 是阶段 0 的已验证版本，不代表永久推荐版本。正式实现时仍应通过依赖锁文件和升级流程控制版本。

## 认证策略

MVP 优先支持本机 ChatGPT 管理认证，因为阶段 0 已验证其可读 Codex 限额。

必须区分：

| 认证类型 | 执行 | 限额来源 |
| --- | --- | --- |
| ChatGPT 管理认证 | 支持 | App Server ChatGPT rate limits |
| API key | 后续支持 | API 返回的错误、header 与项目配额 |
| 其他 provider | 非 MVP | 独立 Provider 适配 |

凭据不得进入任务数据库或 Resume Package。

## 结果

正面影响：

- 不依赖 UI；
- 能事件驱动恢复；
- 支持真实的新进程 thread 恢复；
- 运行时版本可控；
- 限额观察可替换。

代价：

- 需要维护一个小型 JSON-RPC 客户端；
- 需要 App Server 契约测试；
- SDK 和 App Server 的错误需规范化成 Orchestrator 事件。

## 后续动作

阶段 1 首先实现：

1. `FakeExecutionAdapter`；
2. `CodexSdkExecutionAdapter` 的接口骨架；
3. `AppServerRateLimitProvider`；
4. 统一错误类型；
5. 协议 Schema 固定与契约测试。

