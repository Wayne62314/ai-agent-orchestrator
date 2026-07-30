# 13 — 阶段 2 实现报告

## 1. 结果

阶段 2 已完成。

系统现在具备真实的单任务恢复闭环：

> Codex 执行 → 持久 Run → 阶段完成或中断 → Checkpoint → 漂移检测 → Resume Package → 恢复 thread → 继续执行 → 验收 → 成功。

真实 Codex 演练已经验证 read-only、workspace-write、thread resume、turn interrupt、限额读取和持久状态协调。

## 2. 新增能力

### 2.1 Run 与租约

`runs` 表新增：

- `provider_run_id`
- `thread_id`
- `lease_owner`
- `lease_expires_at`
- `heartbeat_at`

Run 生命周期：

```text
STARTING → RUNNING → COMPLETED
                   → INTERRUPTED
                   → FAILED
STARTING/RUNNING --租约过期→ ABANDONED
```

租约规则：

- 只有租约持有者可以 heartbeat 或完成 Run；
- heartbeat 延长过期时间；
- 终态清除租约；
- 过期活动 Run 可以被恢复扫描标记为 `ABANDONED`；
- 若对应任务仍为 `RUNNING`，恢复扫描通过事件将任务推进到 `NEEDS_ATTENTION`。

### 2.2 Checkpoint Schema v1

Checkpoint 使用 UTF-8 JSON，包含：

- 任务合同；
- 工作区事实；
- 已完成、进行中和待办事项；
- 决策；
- 暂停原因；
- 下一动作；
- 验收信息；
- 权限；
- provenance 与 SHA-256。

写入流程：

```text
数据库 PENDING
    ↓
同目录临时文件
    ↓
flush + fsync
    ↓
原子 os.replace
    ↓
数据库 READY
```

只有 `READY` Checkpoint 能成为恢复点。加载时同时比较：

- 数据库保存的哈希；
- 文件内嵌哈希；
- 根据当前内容重新计算的哈希。

### 2.3 Resume Package

Resume Package 由已验证 Checkpoint 和当前工作区事实生成，包括：

- 任务合同；
- 当前分支、HEAD 和 dirty files；
- 已验证进度；
- 暂停原因；
- 漂移结论；
- 下一动作；
- 权限边界；
- 结构化输出合同；
- 先前 Codex thread id。

以下漂移阻止恢复：

- 仓库类型或根目录变化；
- 分支变化；
- HEAD 变化；
- 与 relevant files 重叠的内容变化。

仅影响无关文件的变化被标记为 `NON_CONFLICTING`，并显式写入 Resume Package。

### 2.4 正常多阶段继续

新增事件：

```text
VERIFYING --CONTINUATION_REQUIRED→ READY
```

该事件用于“当前阶段完成，但任务合同还有下一阶段”。它避免把正常继续错误记录为测试失败。

### 2.5 Codex SDK 适配器

`CodexSdkExecutionAdapter` 已实现：

- 新建 thread；
- 恢复已有 thread；
- 非阻塞启动 turn；
- 等待并收集结果；
- token usage 结构化；
- turn interrupt；
- read-only、workspace-write 和 full-access 映射；
- SDK 版本与运行时检查；
- 适配器错误归一化；
- 上下文管理和进程关闭。

安全默认：

- 调用方未指定时使用 `read-only`；
- workspace-write 必须显式传入；
- 不使用桌面 UI 自动化；
- 不使用远程 WebSocket。

### 2.6 App Server 限额适配器

实现了本地 stdio JSON-RPC 客户端：

- initialize / initialized 握手；
- 请求 ID 与响应队列；
- 通知队列；
- stdout / stderr 独立读取；
- 请求超时；
- 有界 stderr 缓冲；
- Windows 隐藏子进程；
- 温和关闭和超时终止。

`AppServerRateLimitProvider` 支持：

- `account/rateLimits/read`
- `account/rateLimits/updated`
- 多 bucket 和单 bucket 兼容；
- `usedPercent`
- `resetsAt`
- `rateLimitReachedType`

不会把原始账户响应写进任务数据库。

## 3. 执行协调器

`ExecutionCoordinator` 把外部运行转换为持久状态：

### 启动

1. 验证 Task 为 `READY`；
2. 创建带租约的 `STARTING` Run；
3. 发送 `RUN_REQUESTED`；
4. 启动 Codex turn；
5. 持久化 provider turn id 和 thread id；
6. Run 进入 `RUNNING`。

### 完成

| Codex 结果 | Run 状态 | Task 事件 |
| --- | --- | --- |
| completed | `COMPLETED` | `PHASE_COMPLETED` |
| interrupted | `INTERRUPTED` | `SIGNAL_REQUIRED` |
| failed | `FAILED` | `RUN_FAILED` |

如果 Codex 已启动但数据库绑定失败，协调器会尽力中断外部 turn，避免留下孤儿运行。

## 4. 发现并修复的真实竞态

### 现象

中断请求与 turn 自然完成同时发生时，App Server 可能返回：

```text
no active turn to interrupt
```

阶段 2 初版把它错误分类为 `ADAPTER_ERROR`。

### 修复

该错误现在解释为“中断请求到达前 turn 已经结束”，适配器随后收集真实终态：

- 若已完成，返回 `COMPLETED`；
- 若实际中断，返回 `INTERRUPTED`；
- 不再把竞态误报为执行失败。

已增加回归测试。

## 5. 自动化验证

最终测试覆盖：

- 阶段 1 的所有状态机、去重、并发和审计测试；
- Stage 1 数据库向 Stage 2 前向迁移；
- Run 创建、绑定、heartbeat、完成和过期；
- 非租约持有者拒绝；
- Checkpoint 创建、加载和篡改检测；
- Resume Package 正常生成和冲突阻断；
- Git 实仓库 clean/dirty 快照；
- 分支、HEAD、相关与无关文件漂移；
- Codex SDK start、resume、collect 和 interrupt；
- 中断/自然完成竞态；
- 限额读取和更新通知解析；
- 执行结果到任务事件的转换；
- CLI checkpoint 与 resume 命令。

最终结果：

```text
Ran 41 tests
OK
```

项目同时成功构建并隔离安装：

```text
ai_agent_orchestrator-0.2.0-py3-none-any.whl
```

## 6. 真实 Codex 验证

### 6.1 限额读取

本机 ChatGPT 管理认证：

```text
RATE_LIMIT_BUCKETS=1
USED_PERCENT_PRESENT=true
RESETS_AT_PRESENT=true
PROVIDER=codex-chatgpt
```

实际数值未写入报告。

### 6.2 Start 与 Resume

```text
LIVE_START=PASS
LIVE_RESUME=PASS
THREAD_ID_PERSISTED=true
EXPERIMENT_THREAD_ARCHIVED=true
```

### 6.3 Interrupt

```text
LIVE_INTERRUPT_STATUS=INTERRUPTED
LIVE_INTERRUPT=PASS
EXPERIMENT_THREAD_ARCHIVED=true
```

### 6.4 持久协调器

```text
COORDINATOR_RESPONSE=PASS
RUN_STATE=COMPLETED
TASK_STATE=VERIFYING
LEASE_CLOSED=true
AUDIT_CHAIN_VALID=true
EXPERIMENT_THREAD_ARCHIVED=true
```

### 6.5 完整恢复闭环

在隔离 Git 仓库中执行：

1. 真实 read-only 第一阶段；
2. 进入 `CONTINUATION_REQUIRED`；
3. 保存并验证 Checkpoint；
4. 用户侧增加一个无关文件；
5. 漂移判定为 `NON_CONFLICTING`；
6. 构造 Resume Package；
7. 恢复同一 Codex thread；
8. 真实 workspace-write 创建指定文件；
9. 自动检查文件精确内容；
10. 任务进入 `SUCCEEDED`。

结果：

```text
FIRST_PHASE_COMPLETED=true
CONTINUATION_APPLIED=true
CHECKPOINT_READY=true
DRIFT_KIND=NON_CONFLICTING
THREAD_RESUMED=true
SECOND_RUN_COMPLETED=true
WORKSPACE_WRITE_ACCEPTED=true
FINAL_TASK_STATE=SUCCEEDED
AUDIT_CHAIN_VALID=true
EXPERIMENT_THREAD_ARCHIVED=true
```

## 7. 已知边界

- 活动 SDK TurnHandle 只存在于当前进程内；进程崩溃后依赖租约识别旧 Run，再通过 checkpoint + thread id 启动新 Run。
- `account/rateLimits/updated` 已通过协议模拟测试；没有为了等待真实变化而长时间占用连接。
- 没有主动触发额度耗尽。
- 没有测试 full-access；MVP 不需要它。
- 一个早期极快中断实验产生了空 rollout，官方归档接口拒绝归档该空记录；它不含任务内容或凭据，后续有效实验 thread 均已归档。
- 自动测试、构建、有限修复循环属于阶段 3。

## 8. 阶段 3 入口

建议按顺序实现：

1. 验收策略配置解析；
2. 受限命令执行器；
3. 超时、输出截断和完整日志；
4. Verification 持久化；
5. `CHECKS_PASSED` / retryable / final failure 判定；
6. 有限修复循环；
7. 最终交付报告；
8. CI 结果事件适配。
