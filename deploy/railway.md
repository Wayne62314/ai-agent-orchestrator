# Railway 生产部署手册

## 固定配置

本项目选择 Railway 作为 MVP 的首个长期托管平台。生产服务必须使用单实例和不可变镜像摘要：

```text
ghcr.io/wayne62314/ai-agent-orchestrator@sha256:<v0.8.0-digest>
```

不要使用 `latest`、`0.8.0` 或提交标签直接部署。`v0.8.0` 发布完成并验证来源证明后，才把实际摘要填入 Railway。

服务配置：

| 项目 | 值 |
| --- | --- |
| Service name | `ai-agent-orchestrator` |
| Source | Public Docker image |
| Replicas | `1` |
| Volume mount | `/data` |
| Database | `/data/state.db` |
| Port | `8080` |
| Health check | `/readyz` |
| Public domain target port | `8080` |
| Restart policy | `ON_FAILURE` |

SQLite 不支持多个实例共享同一个文件；不要开启副本或多区域部署。

## 为什么需要 `RAILWAY_RUN_UID=0`

Railway 的新卷以 root 身份挂载，而镜像中的应用用户是 UID `10001`。平台需要以 root 启动镜像，容器入口只对 `/data` 顶层目录执行一次归属校正，随后在执行任何应用命令前切换到 `orchestrator`。发布烟雾测试会验证：

- root 所有的新卷可以写入；
- 实际应用命令的 UID 是 `10001`；
- 未经 root 启动时仍直接使用非特权用户。

这不是让应用长期以 root 运行。不要覆盖镜像 `ENTRYPOINT`。

## 创建资源

此步骤会创建持续计费资源，必须由仓库所有者登录 Railway 并确认套餐与费用上限：

1. 创建空项目和 `production` 环境。
2. 添加 Docker Image 服务，填入已验证的 `v0.8.0` 镜像摘要。
3. 给该服务添加一个卷并挂载到 `/data`。
4. 保持单实例。
5. 在服务 Variables 中设置：

```text
PORT=8080
ORCHESTRATOR_DB=/data/state.db
RAILWAY_RUN_UID=0
ORCHESTRATOR_GITHUB_WEBHOOK_SECRET=<至少 32 字符的随机值>
```

将 webhook 密钥设为 sealed variable。不要把真实值写入仓库、命令历史、PR 或日志。

6. 将 Healthcheck Path 设为 `/readyz`。
7. 生成 Railway 域名，并确认 target port 是 `8080`。
8. 在项目 Usage 中设置可接受的 hard limit。
9. 在服务 Backups 中启用 Daily、Weekly 和 Monthly 三种卷备份计划。
10. 部署前审阅 Railway 的 staged changes，再执行 Deploy。

Railway CLI 可用于创建同等资源，但首次部署建议使用仪表盘逐项审核，避免把付费资源创建和密钥注入隐藏在脚本中。

## 首次验收

先检查公开端点：

```text
curl --fail https://<railway-domain>/healthz
curl --fail https://<railway-domain>/readyz
```

然后在 Railway 日志中确认服务没有权限错误或重复重启，并确认卷中出现 `/data/state.db`。

配置 GitHub Webhook：

- Payload URL：`https://<railway-domain>/webhooks/github`
- Content type：`application/json`
- Secret：与 sealed variable 相同
- SSL verification：启用
- Events：`workflow_run`、`pull_request_review`、`pull_request_review_comment`、`issues`

使用 `demo prepare-ci` 创建一次真实等待，触发 CI 后以 `demo verify-ci` 验证任务、事件收据和审计链。

## 备份恢复演练

生产启用后立即创建一次手动卷备份。恢复演练必须在受控窗口执行：

1. 记录当前镜像摘要、域名和最后一次成功事件；
2. 从 Backups 选择备份并 Restore；
3. 审阅 Railway 创建的新卷和挂载变更；
4. Deploy 后检查 `/readyz`；
5. 核对任务状态和审计链；
6. 记录演练结果，不删除旧卷，直到确认恢复正确。

Railway 恢复卷备份会产生 staged change；恢复前后都必须人工审阅。擦除卷会连同备份一起删除，禁止把 Wipe 当作普通清理操作。

## 回滚

应用回滚只替换为上一份已验证镜像摘要，不更改卷。若新版本包含不向后兼容的数据库迁移，先停止并按对应恢复方案处理，不能盲目启动旧镜像。

## 官方依据

- Railway 服务支持公开 GHCR 镜像：<https://docs.railway.com/services>
- 公开域名、target port 与 HTTPS：<https://docs.railway.com/public-networking>
- 卷挂载与非 root 镜像权限：<https://docs.railway.com/volumes>
- SQLite 卷备份和恢复：<https://docs.railway.com/volumes/backups>
- sealed variables：<https://docs.railway.com/variables>
- 套餐与用量：<https://docs.railway.com/pricing/plans>
- 费用硬上限：<https://docs.railway.com/pricing/cost-control>
