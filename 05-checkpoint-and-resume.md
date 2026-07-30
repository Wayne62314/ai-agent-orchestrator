# 05 — 检查点与恢复协议

## 1. 设计目标

检查点不是聊天摘要，而是一份足以让新执行会话可靠接手的结构化事实包。

它必须区分：

- 用户最初要求；
- 已验证事实；
- Agent 的推断；
- 尚未完成的计划；
- 不得执行的动作。

## 2. Checkpoint Schema

```yaml
schema_version: 1
task:
  id: task_...
  objective: "最终目标"
  acceptance_criteria:
    - "可验证条件"
  scope:
    allowed: []
    forbidden: []

workspace:
  path: "绝对路径"
  vcs:
    type: git
    branch: "feature/example"
    head: "commit sha"
  dirty_files: []
  relevant_files: []
  fingerprint: "hash"

progress:
  completed:
    - item: "已完成事项"
      evidence: "测试、diff 或产物引用"
  in_progress: []
  pending: []
  failed_attempts: []

decisions:
  - decision: "已作出的选择"
    rationale: "原因"
    source: "user | agent | repository"

current_block:
  reason: "暂停原因"
  waiting_for: "具体恢复条件"
  resume_not_before: null
  timeout_at: null

next_action:
  description: "恢复后的第一项动作"
  expected_result: "预期证据"
  risk_level: low

verification:
  last_results: []
  required_checks: []

permissions:
  granted: []
  approvals_required: []

provenance:
  run_id: run_...
  created_at: "ISO-8601"
  payload_hash: "sha256"
```

## 3. Resume Package

恢复时不能原样倾倒全部历史。系统应从检查点和当前工作区生成一次性 Resume Package：

1. **任务合同**：目标、范围、验收标准；
2. **当前事实**：分支、提交、未提交变更、最近测试；
3. **已完成工作**：附证据；
4. **暂停原因**：之前为什么停止；
5. **环境漂移**：检查点后发生了什么变化；
6. **下一动作**：只给出最接近的可执行步骤；
7. **权限边界**：哪些动作允许，哪些必须请求批准；
8. **输出协议**：执行后必须返回的结构化结果。

示例：

```text
你正在恢复任务 task_123 的第 3 次运行。

目标：
为示例服务实现登录，并通过约定测试。

已验证完成：
- 数据模型已添加；证据：测试 auth_model 通过。

当前工作区：
- 分支 feature/login
- HEAD abc123
- 有两处未提交变更，均来自上一轮
- 与检查点相比没有外部漂移

暂停原因：
- 上一执行引擎暂不可用；未发生待确认的外部副作用。

下一步：
- 完成登录处理器，然后运行 auth 相关测试。

约束：
- 不部署，不推送，不删除数据。
- 若需要修改数据库结构，先请求批准。

完成本轮后返回：
- 实际完成项
- 修改文件
- 执行的检查及结果
- 未解决问题
- 推荐的下一状态
```

## 4. 恢复前漂移检测

恢复前至少比较：

- 当前分支和 HEAD；
- 未提交文件列表及内容哈希；
- 关键配置文件；
- 依赖锁文件；
- 上一轮运行进程是否仍存在；
- 是否已有其他任务占用同一工作区。

处理方式：

| 漂移类型 | 默认策略 |
| --- | --- |
| 无漂移 | 正常恢复 |
| 只有预期的上一轮改动 | 正常恢复 |
| 用户新增但不冲突的改动 | 在 Resume Package 中显式说明 |
| 同文件冲突或分支变化 | `NEEDS_ATTENTION` |
| 无法确认外部副作用 | `NEEDS_ATTENTION` |

## 5. 压缩与证据

上下文压缩遵循：

- 仓库事实优先于模型叙述；
- 测试输出摘要必须链接到完整日志；
- 已完成事项必须有证据引用；
- 删除重复对话，不删除用户约束；
- 推断必须标明为推断；
- 原始检查点不可被后续摘要覆盖，只能追加新版本。

## 6. 检查点生成时机

必须生成：

- 进入等待前；
- 请求审批前；
- 每轮执行结束时；
- 验收失败后；
- 即将达到运行时长或资源上限时；
- 收到取消请求时；
- 发生不可恢复异常时。

可选生成：

- 完成重要里程碑时；
- 大规模修改前；
- 执行引擎主动认为上下文即将不足时。

