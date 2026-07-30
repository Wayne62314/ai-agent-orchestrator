# 15 — 阶段 4 实现报告

## 1. 结果

阶段 4 已完成。系统现在具备高风险动作从提出、暂停、审批、精确授权到幂等执行和崩溃对账的完整安全闭环。

```text
RUNNING
  → 提出高风险动作
  → 权限策略 ASK
  → 持久化动作哈希审批
  → WAITING_FOR_APPROVAL
      → 拒绝 → CANCELLED（不创建副作用）
      → 正确哈希批准 → READY
  → 执行前写 PENDING
      → 成功 → SUCCEEDED + 消费审批
      → 结果不明 → UNKNOWN → 人工/外部对账
```

`AC-05` 已通过：生产部署动作在没有正确、未过期且参数完全匹配的批准前，执行函数不会被调用。

## 2. 权限策略

`PermissionPolicy` 支持：

- `allow`：明确允许；
- `ask`：暂停并请求用户批准；
- `deny`：拒绝。

求值规则：

1. 使用点分动作路径，例如 `deployment.production`、`git.push`；
2. 支持父级规则和 `*` 通配符；
3. 部署、远程 Git 写入、删除、认证网络写入、消息和付费动作默认 `ask`；
4. 其他未知动作默认 `deny`；
5. 非法配置按 `deny` 处理。

示例：

```json
{
  "git": {
    "commit": "ask",
    "push": "deny"
  },
  "network": {
    "read_public": "allow",
    "authenticated_write": "ask"
  },
  "deployment": {
    "*": "ask"
  }
}
```

## 3. 动作哈希审批

`ActionRequest` 包含：

- 动作类型；
- 逻辑步骤；
- 规范化参数；
- 风险摘要；
- 回滚计划。

授权哈希为：

```text
SHA-256(action_type + logical_step + canonical_parameters)
```

审批规则：

- 请求可设置 60–86400 秒有效期；
- 重复的同动作审批请求幂等；
- 批准时必须提交用户看到的动作哈希；
- 参数顺序变化不改变哈希；
- 参数值、动作类型或逻辑步骤变化会改变哈希；
- 过期、拒绝或已消费审批不可使用；
- 拒绝会通过状态事件把任务移入 `CANCELLED`。

审批状态和所有决定均写入哈希审计链。

## 4. 副作用幂等账本

SQLite Schema v4 新增 `side_effects` 表，记录：

- `effect_id`；
- `task_id` 与可选 `approval_id`；
- 唯一幂等键；
- 动作类型与逻辑步骤；
- 参数哈希；
- 状态；
- 外部结果标识；
- 脱敏错误；
- 创建与更新时间。

状态机：

```text
PENDING → SUCCEEDED
        → UNKNOWN → SUCCEEDED
                  → FAILED
```

执行规则：

1. 先验证策略和审批；
2. 外部调用前原子写入 `PENDING`；
3. 成功后写外部结果并消费审批；
4. 执行函数抛错时保守标记 `UNKNOWN`；
5. 同幂等键已成功时返回既有结果，不重复调用；
6. 同幂等键为 `PENDING` 或 `UNKNOWN` 时拒绝重放；
7. 重启扫描把过期 `PENDING` 标记为 `UNKNOWN`；
8. 只有明确对账后才能进入 `SUCCEEDED` 或 `FAILED`。

## 5. 敏感信息过滤

统一 `SensitiveDataRedactor` 覆盖：

- 验收完整日志和摘要；
- Checkpoint；
- Resume Package 结构和提示；
- Event payload；
- Audit payload；
- 副作用错误与外部结果标识。

支持过滤：

- 常见 token、密码、API Key、Bearer 凭据；
- GitHub/OpenAI 风格 token；
- AWS Access Key；
- PEM 私钥块；
- 调用方显式提供的敏感值。

`credential_ref`、token 预算和使用量等非凭据元数据不会被误删。任务合同和动作参数若直接携带凭据型值会被拒绝。

## 6. 超时进程树隔离

验收命令改为独立进程组：

- Windows 使用新进程组并在超时时终止 PID 及其子进程树；
- POSIX 使用新 session 并终止整个 process group；
- 终止后继续收集已脱敏的 stdout/stderr；
- 退出状态仍明确记录为超时失败。

真实故障测试启动了会延迟写文件的子进程；父命令超时后，子进程未能继续运行或写出标记文件。

## 7. 命令行入口

```text
agent-orchestrator approval request <task_id> <action_type> \
  --logical-step <step> --parameters <json> \
  --risk <summary> --rollback <plan>

agent-orchestrator approval show <approval_id>
agent-orchestrator approval list <task_id>
agent-orchestrator approval approve <approval_id> \
  --action-hash <sha256> --by <actor>
agent-orchestrator approval deny <approval_id> \
  --action-hash <sha256> --by <actor>

agent-orchestrator effect show <effect_id>
agent-orchestrator effect list <task_id>
agent-orchestrator effect recover-stale
agent-orchestrator effect reconcile <effect_id> \
  --outcome succeeded --external-result-id <id>
```

通用 CLI 不直接执行任意外部副作用；具体集成必须把受控 performer 传给 `SideEffectCoordinator`。

## 8. 故障演练

阶段 4 自动化覆盖：

- 未批准的生产部署；
- 错误动作哈希；
- 参数变化后复用旧审批；
- 过期审批；
- 用户拒绝；
- 重复审批请求；
- 重复成功副作用；
- 外部调用后连接中断；
- `UNKNOWN` 副作用重试；
- 重启时遗留 `PENDING`；
- 对账成功与失败；
- 日志、Checkpoint、Resume、Event 和 Audit 泄密；
- 任务合同直接包含凭据；
- 超时父进程创建后台子进程；
- Schema v1–v3 向 v4 前向迁移；
- 阶段 0–3 全部回归。

最终结果：

```text
Ran 70 tests
OK
```

## 9. 已知边界

- 审批交互当前通过服务 API 和 CLI 提供，尚无本地 Web UI。
- 过滤器覆盖常见凭据格式和敏感键；生产部署仍应使用操作系统密钥存储，并只向编排器传引用。
- 对账逻辑需要具体外部系统适配器提供查询能力；通用核心只负责阻止盲目重放和记录确认结果。
- 阶段 4 不自动推送、合并或部署；这些能力必须通过显式策略、审批和具体受控 performer 接入。

## 10. 阶段 5 入口

下一阶段建议按以下顺序加入可信事件：

1. CI 完成；
2. PR Review 评论；
3. Issue 状态变化；
4. 服务健康检查；
5. 更可靠的限额恢复信号。

每个事件适配器必须定义来源认证、去重键、载荷过滤、满足条件、超时和副作用边界。
