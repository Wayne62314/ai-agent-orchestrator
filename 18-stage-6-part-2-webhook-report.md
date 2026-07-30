# 18 — 阶段 6 第二部分：Webhook 服务与常驻 Worker

## 1. 结论

阶段 6 第二部分的本地实现已完成。编排器现在提供真实 HTTP 服务，可接收 GitHub webhook、在解析前验证 HMAC-SHA256、自动找到唯一满足条件的活动等待，并通过阶段 5 的持久事件协议恢复任务。

服务不要求 webhook URL 包含 `task_id`。GitHub 仓库只需要配置一个稳定入口 `/webhooks/github`；任务路由由数据库中的活动等待决定。

CI 等待不必预先知道 GitHub 的运行 ID。路由同时支持：

- 精确运行：`repo#workflow:<run_id>`；
- 工作流与分支：`repo#workflow:<name>#branch:<branch>`；
- 工作流：`repo#workflow:<name>`；
- 分支：`repo#branch:<branch>`。

事件收据仍保存 GitHub 的精确 run ID，旧等待格式保持兼容。

## 2. HTTP 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/webhooks/github` | 接收 GitHub webhook |
| `GET` | `/healthz` | 进程存活检查 |
| `GET` | `/readyz` | SQLite 可用性检查 |

请求要求：

- `Content-Type: application/json`；
- `Content-Length` 存在且不超过 1 MiB；
- `X-GitHub-Event`；
- `X-GitHub-Delivery`；
- `X-Hub-Signature-256`。

支持 `workflow_run`、`pull_request_review`、`pull_request_review_comment`、`issues` 和 GitHub 配置时发送的 `ping`。

## 3. 自动路由

处理顺序：

```text
限制请求体
  → HMAC-SHA256 验签
  → JSON 解析
  → 适配器提取可信元数据
  → 查询相同 provider / kind / subject 的活动等待
  → 白名单条件匹配
  → 唯一任务恢复
```

结果：

- 唯一匹配：`200`，持久事件进入 `CONSUMED`，任务进入 `READY`；
- 没有匹配：`202`，写全局审计，不改变任务；
- 多个匹配：`409`，写歧义审计，不选择任意任务；
- 签名错误：`401`，不占用 delivery ID；
- 重复合法投递：返回既有收据，不重复恢复。

这避免了固定 webhook URL 与临时任务 ID 的耦合，也避免多个任务等待同一外部条件时随机唤醒。

## 4. 常驻 Worker

`RecoveryWorker` 与 HTTP 服务同时运行，周期执行：

- 扫描超过持久截止时间的 `ACTIVE` 等待；
- 通过 `SIGNAL_TIMEOUT` 将任务升级到 `NEEDS_ATTENTION`；
- 把崩溃遗留的过期 `PENDING` 副作用标记为 `UNKNOWN`；
- 使用停止事件响应服务关闭。

也可以运行一次：

```text
agent-orchestrator --db <path> worker tick
```

## 5. 密钥与日志

- 密钥从 `ORCHESTRATOR_GITHUB_WEBHOOK_SECRET` 环境变量读取；
- CLI 不接受密钥值参数；
- `.env` 被 Git 忽略；
- 错误签名只保存 delivery ID 摘要；
- HTTP 响应不返回事件正文或密钥；
- PR Review/评论正文继续沿用阶段 5 的省略策略。

密钥轮换需要更新部署环境变量并滚动重启服务。GitHub webhook 配置和服务应在短暂重叠窗口内同步修改。

## 6. 容器运行

交付：

- `Dockerfile`；
- `compose.yaml`；
- `.dockerignore`；
- `.env.example`。

容器：

- 使用 Python 3.11；
- 使用 UID 10001 非 root 用户；
- SQLite 位于 `/data/state.db`；
- `/data` 使用命名卷；
- 暴露 8080；
- 通过 `/healthz` 执行容器健康检查；
- 收到 SIGTERM 后停止 HTTP 服务和 Worker。

CI 会实际构建容器镜像，防止 Dockerfile 只存在于文档中。

## 7. 验证

- 全部 90 项测试通过；
- Ruff 通过；
- 真实 TCP/HTTP 请求恢复 CI 等待任务；
- 重复请求返回同一事件收据；
- 错误签名后相同 delivery ID 的合法请求仍可成功；
- 零匹配返回 `202`；
- 两个任务同时匹配返回 `409`，两个任务均保持等待；
- `ping`、Content-Type 和路由错误均有测试；
- 重开 SQLite 后，Worker 可让过期等待进入 `NEEDS_ATTENTION`；
- sdist、wheel 构建和隔离安装验证通过；
- Docker 镜像构建纳入 GitHub Actions。

## 8. 部署边界

代码已经可部署，但当前没有用户指定的云平台、服务器、域名或 TLS 终止位置，因此没有创建公网生产服务，也没有向 GitHub 仓库写入 webhook URL 或密钥。

下一步需要选择一个支持持久卷和长期运行进程的托管目标。确定目标后：

1. 部署容器并挂载持久卷；
2. 配置 HTTPS 域名；
3. 注入 webhook 密钥；
4. 在 GitHub 仓库创建 webhook；
5. 用真实 `workflow_run` 完成最终端到端演练。
