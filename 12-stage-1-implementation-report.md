# 12 — 阶段 1 实现报告

## 1. 结果

阶段 1 已完成，持久状态骨架可运行、可安装、可测试。

当前系统已经能：

- 在 SQLite 中持久保存任务及相关运行模型；
- 通过事件驱动合法状态转换；
- 拒绝非法转换和过期版本写入；
- 对事件 ID 和逻辑去重键进行双重去重；
- 跨进程重新打开数据库并继续读取状态；
- 记录哈希串联的追加式审计日志；
- 检测审计内容被事后修改；
- 通过 CLI 创建、查看、过滤和驱动任务；
- 用 Fake Adapter 验证执行器生命周期；
- 在没有 Codex SDK 时独立运行核心。

阶段 1 不会启动真实 Codex 任务，也不会读取或保存凭据。

## 2. 代码结构

```text
src/agent_orchestrator/
├── models.py                 领域模型和枚举
├── state_machine.py          纯状态转换规则
├── schema.py                 SQLite 前向迁移
├── store.py                  事务、去重和审计链
├── service.py                原子事件处理服务
├── cli.py                    人工与脚本控制入口
└── adapters/
    ├── base.py               Provider 中立接口
    ├── fake.py               测试执行器
    ├── codex_sdk.py          Codex SDK 阶段 2 边界
    └── app_server_limits.py  限额 Provider 阶段 2 边界
```

测试位于 `tests/`，仓库约束位于 `AGENTS.md`。

## 3. 数据库

第一版迁移创建：

| 表 | 用途 |
| --- | --- |
| `schema_migrations` | 前向迁移版本 |
| `tasks` | 任务合同、状态和乐观版本 |
| `runs` | 每次执行尝试 |
| `checkpoints` | 检查点元数据和哈希 |
| `events` | 去重事件及处理结果 |
| `approvals` | 高风险动作审批 |
| `verifications` | 验收结果 |
| `audit_log` | 追加式哈希审计链 |

每次数据库操作使用独立连接，启用：

- 外键检查；
- `BEGIN IMMEDIATE` 写事务；
- busy timeout；
- 异常回滚；
- 操作结束显式关闭连接。

## 4. 状态一致性

### 原子事件处理

一次事件处理在同一事务内完成：

1. 读取任务及版本；
2. 插入事件并检查去重；
3. 验证 expected version；
4. 验证状态转换；
5. 更新任务和版本；
6. 标记事件结果；
7. 写入审计链；
8. 提交事务。

中途失败会整体回滚，不会留下“状态已变但事件未记”的半完成结果。

### 去重

系统同时约束：

- `event_id` 唯一；
- `dedupe_key` 唯一；
- 重试内容必须与首次事件的 task、type、source、dedupe key 和 payload 一致。

相同事件重投只返回已有结果，不增加任务版本，也不追加第二条状态转换审计。

### 并发

任务使用单调递增的 `version`。调用方可提供 `expected_version`；版本过期时事件被记录为 `REJECTED`，状态保持不变。

## 5. 审计链

每个任务的审计记录形成独立 SHA-256 链：

```text
GENESIS → TASK_CREATED → STATE_TRANSITIONED → ...
```

哈希覆盖：

- audit id；
- task/run id；
- 时间；
- 类型；
- 规范化 payload；
- 前一条哈希。

该机制能发现本地数据库中的事后篡改，但它不是外部签名或不可否认证明。以后如需更强保证，可把周期性根哈希写到独立存储。

## 6. CLI

已支持：

```text
agent-orchestrator init
agent-orchestrator task create
agent-orchestrator task show
agent-orchestrator task list
agent-orchestrator event emit
agent-orchestrator event allowed
agent-orchestrator audit list
agent-orchestrator audit verify
```

所有命令支持 `--db` 指定数据库，支持 `--json` 供脚本读取。

非法事件返回非零退出状态，并保留拒绝原因。

## 7. 适配器边界

`ExecutionAdapter` 定义：

- `start`
- `inspect`
- `interrupt`
- `collect`

`RateLimitProvider` 定义：

- `read`

`FakeExecutionAdapter` 已可完成、失败和中断模拟。

`CodexSdkExecutionAdapter` 和 `AppServerRateLimitProvider` 当前只负责依赖与运行时检查。真实调用明确抛出阶段 2 未实现错误，避免阶段 1 意外消耗额度。

## 8. 验证结果

### 自动化测试

结果：

```text
Ran 21 tests
OK
```

覆盖：

- 正常状态路径；
- 等待与恢复；
- 所有非终态取消；
- 终态保护；
- 重复事件；
- 去重键和事件 ID 冲突；
- 非法状态转换；
- 过期版本；
- 数据库重复初始化；
- 新 Store 实例恢复；
- 完整成功生命周期；
- 任务过滤；
- 审计链验证与篡改检测；
- CLI 多命令持久状态；
- Fake Adapter 完成与中断；
- 限额 Provider 环境检查。

### 独立进程验证

使用三个独立 CLI 进程：

1. 创建并验证任务；
2. 重新打开数据库读取任务；
3. 再次打开数据库验证审计链。

结果：

```text
TASK_CREATED=True
REOPENED_STATE=READY
REOPENED_VERSION=1
AUDIT_CHAIN_VALID=True
```

### 打包验证

项目成功构建为 Python wheel 并隔离安装：

```text
ai_agent_orchestrator-0.1.0-py3-none-any.whl
```

## 9. 尚未实现

以下属于阶段 2：

- 真实 Codex SDK thread start/run/resume；
- App Server 常驻连接与限额更新通知；
- Run 租约和心跳；
- Checkpoint Schema v1 文件生成；
- Resume Package；
- 工作区 Git 漂移检测；
- 执行结果转换为状态事件；
- 真实 turn interrupt 行为测试。

## 10. 阶段 2 建议顺序

1. 完成 Run repository 和租约；
2. 实现 Checkpoint 文件写入与哈希；
3. 实现 Resume Package；
4. 接入 Codex SDK 的 read-only 真实运行；
5. 接入 thread resume；
6. 接入 App Server 限额读取和更新通知；
7. 用受控任务验证 interrupt；
8. 再允许 workspace-write。

