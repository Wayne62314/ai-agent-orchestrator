# 07 — 交付路线

## 阶段 0：关键可行性验证（已完成）

目标：确定执行引擎是否存在稳定、合规、可观测的自动化入口。

需要验证：

- 如何启动一次执行；
- 如何传入恢复上下文；
- 如何识别完成、等待、失败和额度受限；
- 是否能获取结构化结果；
- 是否能安全取消；
- 官方接口或 CLI 的使用限制；
- 是否存在可靠的额度/限流信息。

退出标准：

- [x] 选定一个接入方式；
- [x] 列出支持与不支持能力；
- [x] 完成一次“关闭连接 → 新连接恢复”实验；
- [x] 验证额度状态读取，并确认定时回查作为容错策略。

结论和证据见：

- [09-stage-0-feasibility-report.md](./09-stage-0-feasibility-report.md)
- [10-execution-adapter-decision.md](./10-execution-adapter-decision.md)
- [11-stage-0-experiment-log.md](./11-stage-0-experiment-log.md)

## 阶段 1：持久状态骨架（已完成）

目标：进程重启后仍能正确识别任务状态。

交付：

- [x] Task、Run、Checkpoint、Event 数据表；
- [x] Approval 与 Verification 预留数据表；
- [x] 状态转换服务；
- [x] 事件 ID 与去重键双重去重；
- [x] 命令行创建、查看和驱动任务；
- [x] 哈希串联的追加式审计日志；
- [x] 执行与限额 Provider 接口；
- [x] 不调用真实模型的 Fake Adapter。

退出标准：

- [x] 状态机和持久层共 21 项测试通过；
- [x] 独立进程重开数据库后状态一致；
- [x] 重复事件不会重复转换；
- [x] Python wheel 构建与隔离安装成功；
- [x] 审计内容被篡改时能够检测。

实现证据见 [12-stage-1-implementation-report.md](./12-stage-1-implementation-report.md)。

## 阶段 2：单任务执行闭环（已完成）

目标：完成一次真实代码任务的执行、暂停和恢复。

交付：

- [x] Codex SDK 执行引擎适配器；
- [x] start、collect、resume 和 interrupt；
- [x] Run 租约、心跳、关闭与过期恢复；
- [x] Checkpoint Schema v1；
- [x] 两阶段 Checkpoint 原子写入与哈希验证；
- [x] Resume Package 生成器；
- [x] 正常阶段继续事件与人工恢复事件；
- [x] Git 工作区漂移检测；
- [x] App Server 限额读取和更新通知适配器；
- [x] read-only 与显式 workspace-write。

退出标准：

- [x] 重复事件仍保持幂等；
- [x] 分支、HEAD 和相关文件漂移会阻止恢复；
- [x] 非冲突漂移会写入 Resume Package；
- [x] 新执行轮次可使用 Resume Package 和同一 thread 继续；
- [x] 真实 workspace-write 产物通过验收；
- [x] 端到端任务进入 `SUCCEEDED`。

实现证据见 [13-stage-2-implementation-report.md](./13-stage-2-implementation-report.md)。

## 阶段 3：自动验收与修复（已完成）

目标：任务只能在有证据时完成。

交付：

- [x] 验收策略配置；
- [x] 无 shell 的受限命令执行；
- [x] 命令超时、输出截断与完整日志捕获；
- [x] Verification 数据库迁移与证据持久化；
- [x] 有限修复循环；
- [x] 最终交付报告。

退出标准：

- [x] 通过 `AC-04`；
- [x] 失败不会被误标成功；
- [x] 超过重试预算会升级给用户。

实现证据见 [14-stage-3-implementation-report.md](./14-stage-3-implementation-report.md)。

## 阶段 4：审批与安全加固（已完成）

目标：支持长时间运行而不扩大授权风险。

交付：

- [x] `allow / ask / deny` 权限策略；
- [x] 绑定动作哈希与有效期的批准记录；
- [x] `PENDING / SUCCEEDED / UNKNOWN / FAILED` 幂等副作用账本；
- [x] 日志、Checkpoint、Resume Package、事件和审计敏感信息过滤；
- [x] 超时进程树终止；
- [x] 审批、崩溃窗口、重复执行和敏感信息故障演练。

退出标准：

- [x] 通过 `AC-05`；
- [x] 旧审批不可复用到变化后的动作；
- [x] 不确定的外部副作用不会被自动重放。

实现证据见 [15-stage-4-implementation-report.md](./15-stage-4-implementation-report.md)。

## 阶段 5：事件扩展

只有前四阶段稳定后再加入：

- CI 完成；
- PR 评论；
- Issue 状态变化；
- 服务健康检查；
- 更可靠的供应商限流恢复信号。

每种事件都必须定义：

- 来源认证；
- 去重键；
- 满足条件；
- 超时行为；
- 是否携带不可信内容。

## 建议的首个演示

选择一个小型示例仓库，任务为：

> 新增一个带测试的功能，完成一半后强制暂停编排器；修改一处无冲突文件；重启后由新执行会话恢复，完成代码并通过测试。

这个演示同时覆盖：

- 持久化；
- 新会话恢复；
- 工作区漂移；
- 自动验收；
- 最终报告。

## 暂不承诺时间

在阶段 0 完成前不估算完整开发周期。执行引擎的可调用性、状态可观测性和平台政策，是本项目最大的外部不确定性。
