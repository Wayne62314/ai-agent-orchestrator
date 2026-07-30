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

## 阶段 5：事件扩展（已完成）

交付：

- [x] GitHub CI 完成事件；
- [x] GitHub PR Review 与评论事件；
- [x] GitHub Issue 状态变化事件；
- [x] 服务健康检查事件；
- [x] App Server 供应商限流恢复信号；
- [x] HMAC-SHA256 webhook 来源认证；
- [x] `provider + delivery_id` 去重与冲突检测；
- [x] 持久化等待条件、截止时间和重启恢复；
- [x] `SIGNAL_TIMEOUT` 超时升级；
- [x] PR 评论正文隔离与内容摘要。

退出标准：

- [x] 只有认证且满足精确白名单条件的事件能使任务进入 `READY`；
- [x] 重复投递不会重复恢复；
- [x] 未匹配事件、签名错误和超时均有持久化结果与审计证据；
- [x] 凭据和不可信评论正文不进入 SQLite；
- [x] CI、PR、Issue、健康和限流五类信号均通过测试。

实现证据见 [16-stage-5-implementation-report.md](./16-stage-5-implementation-report.md)。

## 阶段 6：真实部署与端到端演示

### 第一部分：CI 与仓库保护基础（已完成）

- [x] GitHub Actions 跨平台测试矩阵；
- [x] Ruff 高置信度规则门禁；
- [x] sdist、wheel 构建和安装验证；
- [x] 稳定汇总检查 `CI`；
- [x] Actions 最小权限与完整 SHA 固定；
- [x] Dependabot Actions 更新；
- [x] 仓库公开并启用 `main` 分支保护。

实施记录见 [17-stage-6-part-1-ci-report.md](./17-stage-6-part-1-ci-report.md)。

### 第二部分：真实 Webhook 服务（实现完成）

- [x] GitHub Webhook HTTP 接入口；
- [x] 仓库级活动等待自动路由；
- [x] 环境变量密钥注入；
- [x] 常驻等待扫描与恢复 Worker；
- [x] CI 完成触发真实 HTTP 任务恢复；
- [x] 重启、重复投递、错误签名、歧义和超时端到端演练；
- [x] Dockerfile、Compose、健康和就绪检查；
- [ ] 部署到公开 HTTPS 地址并配置真实 GitHub Webhook。

实现证据见 [18-stage-6-part-2-webhook-report.md](./18-stage-6-part-2-webhook-report.md)。公开部署需要先选择托管目标和域名，不在本地实现中假设。

### 第三部分：真实公网 Webhook 演练（已完成）

- [x] 提供可重复的真实 CI 等待准备与证据验收命令；
- [x] CI 支持手工触发，便于部署后复验；
- [x] 修复服务接入后暴露的 `effect` 子命令不可达问题；
- [x] 通过临时公开 HTTPS 地址接收真实 GitHub `workflow_run`；
- [x] 持久化已认证事件收据并将等待任务恢复到 `READY`；
- [x] 清理临时 Webhook 和隧道，不把测试端点当成生产部署；
- [ ] 选择支持持久卷、稳定域名和长期进程的生产托管目标。

演练证据见 [19-stage-6-part-3-public-demo-report.md](./19-stage-6-part-3-public-demo-report.md)。临时公网隧道只用于验收，不承诺 SLA，不能替代长期生产托管。

### 第四部分：版本化容器发布与生产运行基础（实现完成）

- [x] 仅由 `main` 上的语义版本标签触发发布；
- [x] 校验 Git 标签与 Python 包版本完全一致；
- [x] 发布 GHCR 版本标签和提交 SHA 标签，不发布可变 `latest`；
- [x] 取得不可变镜像摘要并生成 GitHub 构建来源证明；
- [x] 按摘要拉取并验证版本和非 root 运行用户；
- [x] GitHub Actions 使用显式最小权限和完整提交 SHA；
- [x] 提供摘要固定、只读文件系统、移除 Linux capabilities 的生产 Compose；
- [x] 部署前拒绝可变标签、占位密钥、非法端口和过宽文件权限；
- [x] 补齐发布、验收、备份、恢复、回滚和监控手册；
- [x] 合并后创建 `v0.7.0` 标签并验证首个真实镜像发布；
- [x] 决定当前不创建长期托管资源，改用 Windows 本地部署。

实现说明见 [20-stage-6-part-4-production-release-report.md](./20-stage-6-part-4-production-release-report.md)。

### 第五部分：首个 GHCR 镜像发布（已完成）

- [x] PR #7 正确合并，主分支独立 CI 全部通过；
- [x] 注释标签 `v0.7.0` 精确指向合并提交；
- [x] GHCR 发布工作流成功；
- [x] `0.7.0` 与提交 SHA 标签解析到同一镜像摘要；
- [x] 发布后按摘要完成版本和非 root 用户烟雾测试；
- [x] SLSA v1 构建来源证明已签名并写入 GitHub 与 OCI Registry；
- [x] 使用严格来源、引用、提交和工作流约束验证来源证明；
- [x] 匿名 Registry 请求验证镜像包当前为 Public；
- [x] 决定当前不执行云端生产部署，改用 Windows 本地部署。

发布证据见 [21-stage-6-part-5-first-release-report.md](./21-stage-6-part-5-first-release-report.md)。

### 第六部分：Windows 本地优先部署（实现完成）

- [x] 撤销 PR #9 的 Railway 路线，不重写 Git 历史；
- [x] 不依赖 Docker 或云账号的本地 Python 运行环境；
- [x] 默认只监听 `127.0.0.1`；
- [x] 本地随机密钥和当前用户文件权限；
- [x] 启动、停止、状态和日志脚本；
- [x] 可选的登录自启动计划任务；
- [x] 每日备份计划任务和保留策略；
- [x] SQLite 在线一致性备份与完整性验证；
- [x] 停机、显式确认和恢复前快照保护；
- [x] 在目标 Windows 电脑完成安装、备份、恢复和重启演练。

实现说明见 [22-stage-6-part-6-local-deployment-report.md](./22-stage-6-part-6-local-deployment-report.md)。

## 阶段 7：产品定义与桌面架构（已完成）

- [x] 完成用户旅程、信息架构和主要界面线框；
- [x] 选定 Tauri 2、React/TypeScript、Python sidecar 和 NSIS；
- [x] 定义桌面壳、私有 RPC、后台进程和 SQLite 边界；
- [x] 定义 Codex 登录、Windows keyring 凭据和失效恢复；
- [x] 定义安装目录、用户数据目录和迁移契约；
- [x] 定义 Git worktree 任务隔离；
- [x] 完成无本地监听端口的桌面安全设计；
- [x] 通过架构评审。

设计和评审证据见：

- [24-stage-7-product-architecture.md](./24-stage-7-product-architecture.md)
- [25-stage-7-user-experience-spec.md](./25-stage-7-user-experience-spec.md)
- [26-stage-7-completion-report.md](./26-stage-7-completion-report.md)

## 阶段 8：真实 Codex 执行主链

- [ ] 本地安装和验证 Codex SDK；
- [ ] 产品级任务分派、暂停、恢复和取消；
- [ ] 自动处理 Run 心跳、租约和重启恢复；
- [ ] 接通额度等待、自动验收和有限修复；
- [ ] 实施单活动任务互斥和 worktree 生命周期；
- [ ] 通过真实 Codex 端到端验收。

## 阶段 9：桌面 UI

- [ ] 首次启动、Codex 登录和项目选择；
- [ ] 任务创建、状态、进度和控制；
- [ ] 审批中心；
- [ ] 日志、检查点、验收证据和报告；
- [ ] 备份恢复、设置和本地通知；
- [ ] 完整流程无需终端。

## 阶段 10：Windows 安装与打包

- [ ] 自包含 Windows 应用；
- [ ] `.exe` 或 `.msi` 安装程序；
- [ ] 快捷方式和可选登录后启动；
- [ ] 程序与用户数据分离；
- [ ] 手动升级、迁移和卸载数据保护；
- [ ] 可重复、可校验的发布流水线。

## 阶段 11：产品验收与 v1.0 发布

- [ ] 干净 Windows 环境安装、升级和卸载矩阵；
- [ ] 多个真实代码仓库试用；
- [ ] 崩溃、重启、额度和恢复演练；
- [ ] 安全、隐私、可访问性和依赖审查；
- [ ] 真实用户试用与缺陷修复；
- [ ] 发布 v1.0。

完整范围、完成定义和延期版本见 [23-productization-roadmap.md](./23-productization-roadmap.md)。

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
