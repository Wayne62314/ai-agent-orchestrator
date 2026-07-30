# AI Agent Orchestrator

一个面向长时间开发任务的、可恢复（resumable）的 AI Agent 编排器。

它不试图重新实现 Codex，而是负责在执行引擎暂时不可用、会话中断、CI 等待或需要人工审批时，可靠地保存任务状态，并在条件满足后安全地恢复工作。

## 当前结论

第一版应验证一个核心闭环：

> 任务执行 → 生成检查点 → 因外部条件暂停 → 收到恢复信号 → 重建上下文 → 继续执行 → 自动验收 → 请求人工审阅。

MVP 不把“精确读取 Codex 剩余额度”作为成立前提。额度恢复、定时器、CI 完成和人工批准，都被抽象成可插拔的恢复信号。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [01-product-brief.md](./01-product-brief.md) | 产品定位、用户、价值和边界 |
| [02-mvp-requirements.md](./02-mvp-requirements.md) | MVP 范围、需求和验收标准 |
| [03-system-architecture.md](./03-system-architecture.md) | 组件、数据模型和整体架构 |
| [04-state-machine.md](./04-state-machine.md) | 状态定义、转换规则和异常处理 |
| [05-checkpoint-and-resume.md](./05-checkpoint-and-resume.md) | 检查点、Resume Package 和恢复策略 |
| [06-safety-and-reliability.md](./06-safety-and-reliability.md) | 权限、安全、幂等和故障恢复 |
| [07-delivery-roadmap.md](./07-delivery-roadmap.md) | 分阶段实施路线和验证计划 |
| [08-open-questions.md](./08-open-questions.md) | 未决问题、假设和决策门槛 |
| [09-stage-0-feasibility-report.md](./09-stage-0-feasibility-report.md) | 阶段 0 调研结论、能力矩阵与证据 |
| [10-execution-adapter-decision.md](./10-execution-adapter-decision.md) | 执行引擎接入架构决策 |
| [11-stage-0-experiment-log.md](./11-stage-0-experiment-log.md) | 本机实验环境、步骤与结果 |
| [12-stage-1-implementation-report.md](./12-stage-1-implementation-report.md) | 阶段 1 实现、验证与阶段 2 入口 |
| [13-stage-2-implementation-report.md](./13-stage-2-implementation-report.md) | 阶段 2 实现、真实 Codex 验证与已知边界 |
| [14-stage-3-implementation-report.md](./14-stage-3-implementation-report.md) | 阶段 3 自动验收、有限修复与交付报告 |
| [15-stage-4-implementation-report.md](./15-stage-4-implementation-report.md) | 阶段 4 权限审批、安全过滤与副作用账本 |
| [16-stage-5-implementation-report.md](./16-stage-5-implementation-report.md) | 阶段 5 可信外部事件、等待条件与超时恢复 |
| [17-stage-6-part-1-ci-report.md](./17-stage-6-part-1-ci-report.md) | 阶段 6 第一部分：GitHub Actions CI 与仓库保护 |
| [18-stage-6-part-2-webhook-report.md](./18-stage-6-part-2-webhook-report.md) | 阶段 6 第二部分：Webhook HTTP 服务与常驻 Worker |
| [19-stage-6-part-3-public-demo-report.md](./19-stage-6-part-3-public-demo-report.md) | 阶段 6 第三部分：真实公网 Webhook 端到端演练 |
| [20-stage-6-part-4-production-release-report.md](./20-stage-6-part-4-production-release-report.md) | 阶段 6 第四部分：版本化容器发布与生产运行基础 |
| [21-stage-6-part-5-first-release-report.md](./21-stage-6-part-5-first-release-report.md) | 阶段 6 第五部分：首个 GHCR 镜像发布证据 |
| [22-stage-6-part-6-local-deployment-report.md](./22-stage-6-part-6-local-deployment-report.md) | 阶段 6 第六部分：Windows 本地优先部署 |
| [23-productization-roadmap.md](./23-productization-roadmap.md) | 阶段 7–11：桌面 UI、真实 Codex 主链、安装打包和 v1.0 发布 |
| [24-stage-7-product-architecture.md](./24-stage-7-product-architecture.md) | 阶段 7 桌面技术、进程、安全、数据与安装架构 |
| [25-stage-7-user-experience-spec.md](./25-stage-7-user-experience-spec.md) | 阶段 7 用户旅程、信息架构、线框与 UI 验收 |
| [26-stage-7-completion-report.md](./26-stage-7-completion-report.md) | 阶段 7 完成证据与阶段 8 入口 |
| [27-stage-8-implementation-report.md](./27-stage-8-implementation-report.md) | 阶段 8 真实 Codex 主链、恢复、Worktree 与端到端证据 |
| [28-stage-9-part-1-ui-foundation-report.md](./28-stage-9-part-1-ui-foundation-report.md) | 阶段 9 第一部分：桌面 UI 基础与私有 RPC |
| [29-stage-9-part-2-tauri-sidecar-report.md](./29-stage-9-part-2-tauri-sidecar-report.md) | 阶段 9 第二部分：Tauri 原生外壳与真实任务控制 |
| [30-stage-9-part-3-login-repository-report.md](./30-stage-9-part-3-login-repository-report.md) | 阶段 9 第三部分：Codex 登录与原生仓库选择 |
| [31-stage-9-part-4-background-coordination-report.md](./31-stage-9-part-4-background-coordination-report.md) | 阶段 9 第四部分：后台完成、心跳与重启恢复 |
| [32-stage-9-part-5-task-details-report.md](./32-stage-9-part-5-task-details-report.md) | 阶段 9 第五部分：任务详情与持久化证据 |
| [CONTEXT.md](./CONTEXT.md) | 产品领域语言与边界 |

## 建议阅读顺序

1. 先读产品简述，确认我们解决的是不是正确问题。
2. 再看 MVP 需求，确认第一版没有过度设计。
3. 然后审阅架构、状态机与恢复协议。
4. 最后根据未决问题做关键选择，再进入原型开发。

## 项目原则

1. **持久状态优先**：不能依赖模型“记得上次做到哪”。
2. **事件驱动优先**：恢复由可验证信号触发，不盲信固定等待时间。
3. **每步可验收**：任务完成必须有命令、测试、产物或人工确认作为证据。
4. **默认最小权限**：自动化能力不能等同于无限授权。
5. **动作必须幂等**：重试不应重复提交、重复发消息或破坏工作区。
6. **人负责高风险决定**：发布、合并、删除、付费与生产变更默认需要批准。

## 当前状态

- 阶段：阶段 9 第五部分已完成
- 首个执行引擎：Codex
- 主要接入：Codex App Server（由 `openai-codex` 0.144.4 提供本地运行时）
- 限额观察：Codex App Server stdio JSON-RPC
- 已实现：产品级任务生命周期、真实 Codex App Server 执行、暂停恢复、重启恢复、额度等待、单活动任务互斥、Git Worktree 隔离、自动验收与有限修复、权限审批、可信事件、GitHub Actions CI、Webhook 服务、本地运行和备份恢复
- 已选桌面方案：Tauri 2 + React/TypeScript + Python sidecar
- 阶段 9 已交付：React/TypeScript UI、Tauri 原生外壳、受控 sidecar、真实任务控制、Codex 登录、原生仓库选择、后台执行恢复和持久化任务详情
- 下一产物：备份恢复、诊断导出、本地通知和无终端用户旅程收尾

## 快速运行

项目要求 Python 3.11 或更高版本。持久化核心没有第三方依赖；真实 Codex 执行使用可选依赖。

Windows 本地长期运行见 [deploy/local/README.md](./deploy/local/README.md)，不需要 Docker 或云平台。

安装：

```text
python -m pip install -e .
```

安装 Codex 执行能力：

```text
python -m pip install -e ".[codex]"
```

初始化数据库：

```text
agent-orchestrator --db .orchestrator/state.db init
```

创建并验证一个任务：

```text
agent-orchestrator --db .orchestrator/state.db task create --title "示例任务" --objective "验证持久状态" --workspace . --ready
```

运行测试：

```text
python -m unittest discover -s tests -v
```

阶段 2–5 运维命令：

```text
agent-orchestrator run show <run_id>
agent-orchestrator run expired
agent-orchestrator run recover-expired
agent-orchestrator checkpoint create <task_id> ...
agent-orchestrator checkpoint latest <task_id> --verify
agent-orchestrator resume build <task_id>
agent-orchestrator verify run <task_id>
agent-orchestrator verify list <task_id>
agent-orchestrator verify report <task_id>
agent-orchestrator approval request <task_id> deployment.production ...
agent-orchestrator approval approve <approval_id> --action-hash <sha256> --by <actor>
agent-orchestrator approval deny <approval_id> --action-hash <sha256> --by <actor>
agent-orchestrator effect list <task_id>
agent-orchestrator effect recover-stale
agent-orchestrator effect reconcile <effect_id> --outcome succeeded
agent-orchestrator wait register <task_id> --provider github --kind ci.completed --subject <subject> --condition '{"conclusion":"success"}' --timeout-seconds 3600
agent-orchestrator wait list <task_id>
agent-orchestrator wait expire
agent-orchestrator external-event list <task_id>
agent-orchestrator worker tick
```

验收策略示例：

```json
{
  "checks": [
    {
      "name": "unit-tests",
      "command": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
      "required": true,
      "timeout_seconds": 300,
      "max_output_chars": 12000
    }
  ],
  "max_repair_attempts": 2
}
```

验收命令使用参数数组并以 `shell=False` 执行；子进程只获得必要环境变量。摘要可以截断，但完整日志会写入任务工作区的 `.orchestrator/logs`。必选检查全部通过前，任务不能进入 `SUCCEEDED`。

真实 Codex 执行默认使用 `read-only`。自动修复需要调用方显式选择 `workspace-write`；远程 App Server WebSocket 不在 MVP 范围。

阶段 4 的高风险动作默认采用 `ask`，未知普通动作默认 `deny`。审批绑定动作类型、逻辑步骤和规范化参数的 SHA-256；任何参数变化都会使旧审批失效。外部副作用执行前先持久化，结果不明时进入 `UNKNOWN`，必须对账后才能继续。

阶段 5 的 webhook 在解析前验证 HMAC-SHA256，并按 `provider + delivery_id` 去重。恢复必须同时匹配来源、事件类型、主题和白名单条件。PR Review/评论正文不会持久化或参与恢复判断，只保存内容摘要；签名错误、条件不匹配和等待超时都不会静默恢复任务。

## Webhook 服务

服务从环境变量读取 GitHub webhook 密钥，密钥不会出现在命令参数、数据库或日志中：

```text
ORCHESTRATOR_GITHUB_WEBHOOK_SECRET=<long-random-secret>
agent-orchestrator --db .orchestrator/state.db serve --host 127.0.0.1 --port 8080
```

HTTP 入口：

```text
POST /webhooks/github
GET  /healthz
GET  /readyz
```

GitHub webhook URL 必须使用公开 HTTPS 地址。订阅 `workflow_run`、`pull_request_review`、`pull_request_review_comment` 和 `issues`。服务先验签，再按来源、事件类型、主题和条件寻找唯一活动等待；零匹配返回 `202`，多匹配返回 `409`，两者都不会恢复任务。

Docker Compose：

```text
copy .env.example .env
# 编辑 .env，替换 webhook 密钥
docker compose up --build
```

容器使用非 root 用户，SQLite 保存在命名卷中，并由 `/healthz` 提供健康检查。

准备并验收一次真实 GitHub `workflow_run` 演练：

```text
agent-orchestrator --db .orchestrator/demo.db demo prepare-ci \
  --repository OWNER/REPO \
  --workflow CI \
  --branch BRANCH \
  --workspace .

agent-orchestrator --db .orchestrator/demo.db demo verify-ci <task_id>
```

`prepare-ci` 会创建任务并进入 `WAITING_FOR_SIGNAL`。只有匹配仓库、工作流、分支且结论为成功的已验签事件才能恢复任务；`verify-ci` 会同时核对任务、等待、事件收据和审计哈希链。
