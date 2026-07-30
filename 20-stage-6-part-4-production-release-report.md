# 阶段 6 第四部分：版本化容器发布与生产运行基础

## 1. 结论

项目具备了从受保护主分支发布可验证容器镜像的基础。合并本部分后，创建 `v0.7.0` 标签会构建并推送 GHCR 镜像，生成 GitHub 构建来源证明，再按不可变摘要拉取镜像进行烟雾测试。

本部分不创建云主机、域名或付费资源。当前环境没有任何云平台凭据，生产托管选择仍需用户确认。

## 2. 发布约束

发布工作流只接受 `vMAJOR.MINOR.PATCH` 标签，并在接触 Registry 前验证：

- 标签提交必须属于远端 `main`；
- 标签版本必须与 `pyproject.toml` 一致；
- 工作流只由标签 push 触发，PR 不具备发布能力。

发布镜像：

```text
ghcr.io/wayne62314/ai-agent-orchestrator:0.7.0
ghcr.io/wayne62314/ai-agent-orchestrator:sha-<commit>
```

不发布 `latest`。部署必须使用：

```text
ghcr.io/wayne62314/ai-agent-orchestrator@sha256:<digest>
```

## 3. 供应链安全

- Registry 凭据使用短期 `GITHUB_TOKEN`，通过标准输入传给 Docker；
- 权限显式限制为 `contents: read`、`packages: write`、`attestations: write` 和 `id-token: write`；
- 仅使用 GitHub 官方 `actions/checkout` 和 `actions/attest`；
- 两个 Action 都固定到完整 40 位提交 SHA；
- 来源证明附加到 OCI Registry；
- 发布后按摘要拉取并验证应用版本；
- 验证容器用户为 `orchestrator`，不会以 root 运行。

来源证明可使用 GitHub CLI 验证：

```text
gh attestation verify \
  oci://ghcr.io/wayne62314/ai-agent-orchestrator@sha256:<digest> \
  --repo Wayne62314/ai-agent-orchestrator
```

## 4. 生产 Compose

`deploy/compose.production.yaml` 与开发 Compose 分离：

- 禁止本地构建，必须提供镜像摘要；
- 只绑定到 `127.0.0.1`，由稳定 HTTPS 入口反向代理；
- 根文件系统只读；
- 启用 `no-new-privileges`；
- 移除全部 Linux capabilities；
- 仅 `/data` 持久卷和受限 `/tmp` 可写；
- 启用健康检查和自动重启。

部署前校验器会实际拒绝可变标签、占位或过短密钥、非法端口，以及 Linux 上权限过宽的环境文件，并且不会输出密钥。

服务仍采用单实例 SQLite。多个副本不能共享同一个 SQLite 文件。

## 5. 运维手册

`deploy/README.md` 包含：

- 标签发布步骤；
- 构建来源验证；
- 单机 Compose 部署；
- HTTPS 与 GitHub Webhook 配置；
- 健康和端到端验收；
- SQLite 一致性备份与恢复；
- 镜像摘要回滚；
- 最低监控项。

发布前不能从代码仓库断言 GHCR 包的最终可见性，必须在首次发布后检查实际结果。本项目首次发布后，包被 GitHub 标记为 Public，匿名 Registry 拉取验证成功；执行过程中没有调用包可见性修改接口。实际结果记录在 [21-stage-6-part-5-first-release-report.md](./21-stage-6-part-5-first-release-report.md)。

## 6. 验证

- `python -m unittest discover -s tests -v`：100 项通过；
- `python -m ruff check src tests deploy`：通过；
- `git diff --check`：通过；
- `actionlint 1.7.12`：CI 与发布工作流语法、表达式和结构检查通过；
- 工作流安全结构测试：5 项通过；
- 生产环境输入校验测试：4 项通过；
- 本机没有 Docker，因此镜像构建由 PR 的 GitHub Actions 验证；
- 真正的 push、来源证明和按摘要拉取必须等合并后创建 `v0.7.0` 标签验证。

## 7. 下一步

1. 选择支持稳定 HTTPS、持久卷和常驻进程的托管平台；
2. 创建生产资源并固定已验证镜像摘要；
3. 配置密钥、备份和监控；
4. 执行真实 Webhook 生产验收。
