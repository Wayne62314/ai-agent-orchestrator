# 生产运行手册

当前默认选择 Windows 本地部署，见 [local/README.md](./local/README.md)。本文件中的容器发布和单机 Compose 仍作为可选的未来部署能力保留，不代表需要使用云平台。

## 发布模型

只有符合 `vMAJOR.MINOR.PATCH` 的标签会触发镜像发布。发布工作流还会确认：

1. 标签提交属于 `main`；
2. 标签版本与 `pyproject.toml` 一致；
3. 镜像推送到 `ghcr.io/wayne62314/ai-agent-orchestrator`；
4. 同时生成版本标签和 `sha-<commit>` 标签；
5. 取得不可变镜像摘要并生成 GitHub 构建来源证明；
6. 按摘要拉取镜像，验证版本和非 root 用户。

生产部署必须固定 `sha256` 摘要。标签只用于发现版本，不作为部署身份。

## 发布步骤

合并发布 PR 并确认 `main` 的 CI 通过后：

```text
git switch main
git pull --ff-only origin main
git tag --annotate v0.7.0 --message "Release v0.7.0"
git push origin v0.7.0
```

标签是发布动作，不应在 PR 合并前创建。镜像发布后验证来源：

```text
gh attestation verify \
  oci://ghcr.io/wayne62314/ai-agent-orchestrator@sha256:<digest> \
  --repo Wayne62314/ai-agent-orchestrator
```

首次发布的 GHCR 包默认为私有。若希望匿名拉取，需要在 GitHub 包设置中明确改为 Public；该操作不可逆，不由工作流自动执行。

## 单机部署

主机需要 Docker Engine、Compose、持久磁盘和 HTTPS 反向代理。复制环境变量模板，但不要提交实际文件：

```text
copy deploy\production.env.example deploy\.env
python deploy/validate_environment.py deploy/.env
docker compose --env-file deploy/.env -f deploy/compose.production.yaml up -d
```

Linux 上还必须将环境文件权限设为 `0600`。校验器会拒绝可变镜像标签、占位或过短密钥、非法端口，以及权限过宽的 Linux 环境文件；它不会打印密钥值。

Compose 只把服务绑定到 `127.0.0.1`。使用 Caddy、Nginx、负载均衡器或托管平台的 HTTPS 入口转发：

```text
https://<stable-domain>/webhooks/github -> http://127.0.0.1:8080/webhooks/github
```

GitHub Webhook：

- Content type：`application/json`
- Secret：与 `ORCHESTRATOR_GITHUB_WEBHOOK_SECRET` 完全一致
- SSL verification：启用
- Events：按需选择 `workflow_run`、`pull_request_review`、`pull_request_review_comment`、`issues`

## 验收

```text
curl --fail https://<stable-domain>/healthz
curl --fail https://<stable-domain>/readyz
docker compose --env-file deploy/.env -f deploy/compose.production.yaml ps
```

随后使用 `demo prepare-ci` 创建等待，并用真实 CI 完成事件验证 `demo verify-ci`。

## 备份与恢复

SQLite 位于命名卷的 `/data/state.db`。备份前应暂停写入或使用 SQLite 在线备份 API，不能在写入过程中直接复制数据库文件。至少保留：

- 数据库备份；
- 当前镜像摘要；
- 部署配置；
- Webhook 密钥的安全备份；
- 最近一次恢复演练记录。

恢复时先还原数据库，再按原摘要启动镜像，最后检查 `/readyz` 和审计链。

## 回滚

回滚不重建镜像，也不改数据库：

1. 将 `ORCHESTRATOR_IMAGE` 改为上一已验证摘要；
2. 重新执行 Compose；
3. 检查健康、就绪和任务状态；
4. 若数据库 Schema 已发生不兼容升级，停止并执行对应版本的恢复方案，不盲目启动旧二进制。

## 监控

最低生产监控项：

- `/healthz` 和 `/readyz`；
- 进程/容器重启次数；
- 持久卷可用空间与备份新鲜度；
- Webhook 非 `2xx` 比例；
- `NEEDS_ATTENTION` 任务数量；
- 过期等待和 `UNKNOWN` 副作用；
- GitHub Webhook 最近投递结果。

当前应用仍是单实例 SQLite 架构。不要在多个副本之间共享同一个 SQLite 文件。
