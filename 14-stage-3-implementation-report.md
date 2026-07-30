# 14 — 阶段 3 实现报告

## 1. 结果

阶段 3 已完成。任务现在只能在必选验收检查具有持久化通过证据时进入 `SUCCEEDED`。

完整闭环为：

```text
RUNNING
  → PHASE_COMPLETED
  → VERIFYING
  → 执行验收策略
      → 必选检查全部通过 → CHECKS_PASSED → SUCCEEDED
      → 失败且有预算 → CHECKS_FAILED_RETRYABLE → READY → 修复 Run
      → 失败且预算耗尽 → CHECKS_FAILED_FINAL → NEEDS_ATTENTION
  → 生成最终交付报告
```

## 2. 验收策略

每个任务可定义多个检查：

- 唯一名称；
- 参数数组形式的命令；
- 必选或可选；
- 单项超时；
- 摘要最大字符数；
- 最大自动修复次数。

兼容阶段 1 已保存的 `commands` 字符串列表，但内部仍拆分为参数数组，绝不使用 shell。

策略边界：

- 超时 1–3600 秒；
- 摘要 256–1,000,000 字符；
- 自动修复 0–20 次；
- 空命令、重复名称和疑似包含凭据的参数会被拒绝。

## 3. 受限命令执行

`ConstrainedCommandExecutor` 提供：

- `shell=False`；
- 固定在任务工作区执行；
- stdin 关闭；
- stdout/stderr 分离捕获；
- 最小化子进程环境变量；
- 明确超时和失败状态；
- UTF-8 容错解码；
- 原子写入完整日志；
- 头尾保留的有界摘要。

日志默认位置：

```text
<workspace>/.orchestrator/logs/<task_id>/attempt-<n>/
```

摘要被截断时会明确标记，并保留完整日志路径。

## 4. Verification 持久化

SQLite Schema v3 为每条验收证据增加：

- `attempt`；
- `command_json`；
- `timed_out`；
- `output_truncated`；
- `duration_ms`；
- `started_at` / `ended_at`。

每条结果先写数据库和哈希审计日志，再允许状态转换。进程重启后仍可查询每轮证据。

数据库只保存状态、退出码、超时、耗时、截断标记和日志路径等元数据，不保存原始 stdout/stderr，避免命令输出中的凭据进入持久状态。

## 5. 有限修复

`LimitedRepairLoop` 的规则：

1. 仅从 `VERIFYING` 运行验收；
2. 首次失败消耗一次可用修复机会；
3. 修复动作必须通过正常持久 Run 路径把任务带回 `VERIFYING`；
4. 每轮重新执行完整验收策略；
5. 预算耗尽后进入 `NEEDS_ATTENTION`，循环立即停止。

`ExecutionRepairAction` 可直接连接 `ExecutionCoordinator`，生成只包含目标、失败检查和日志路径的修复提示；修复不得删除或弱化验收检查。

## 6. 最终交付报告

任务进入 `SUCCEEDED` 或 `NEEDS_ATTENTION` 时自动生成 Markdown 报告，包含：

- 任务与最终状态；
- 目标；
- 各轮验收结果；
- 必选/可选标记；
- 退出码、超时与耗时；
- 完整日志路径；
- 审计链有效性；
- 成功或需人工处理的结论。

默认位置：

```text
<workspace>/.orchestrator/reports/<task_id>.md
```

## 7. 命令行入口

```text
agent-orchestrator verify run <task_id>
agent-orchestrator verify list <task_id> [--attempt N]
agent-orchestrator verify report <task_id>
```

`verify run` 在检查未最终成功时返回非零退出码，便于外层调度器识别。

## 8. 自动化验证

最终测试覆盖：

- 策略解析、旧格式兼容和非法配置；
- 凭据型命令参数拒绝；
- 成功、非零退出码与命令不存在；
- 超时；
- 摘要截断与完整日志；
- Verification Schema v3 前向迁移；
- 原始命令输出不进入 SQLite；
- 必选检查通过后成功；
- 可选检查失败不阻塞；
- 失败后可修复；
- 修复后重新验收；
- 预算耗尽后升级；
- CLI 运行、查询与报告；
- 阶段 0–2 全部回归。

结果：

```text
Ran 53 tests
OK
```

这组测试覆盖 `AC-04`：失败验收不会被标记成功，且在有限修复后仍失败会进入 `NEEDS_ATTENTION`。

## 9. 已知边界

- 阶段 3 执行本地验收策略；CI、PR 评论和 Issue 事件仍属于阶段 5。
- 子进程超时可终止直接命令；对自行创建并脱离父进程的后台进程，阶段 4 还需加入更强的进程树隔离。
- 日志敏感信息过滤将在阶段 4 加固；当前实现通过最小环境和拒绝凭据型命令参数降低风险。
- 自动修复是否获得 `workspace-write` 必须由调用方显式决定，不会因恢复而自动扩大权限。

## 10. 阶段 4 入口

下一阶段建议依次实现：

1. `allow / ask / deny` 权限策略求值；
2. 绑定规范化动作哈希的审批记录；
3. 日志与 Resume Package 敏感信息过滤；
4. 副作用 `PENDING / SUCCEEDED / UNKNOWN` 幂等账本；
5. 超时进程树隔离；
6. 故障演练与 `AC-05`。
