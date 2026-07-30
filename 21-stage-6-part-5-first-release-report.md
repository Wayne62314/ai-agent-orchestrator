# 阶段 6 第五部分：首个 GHCR 镜像发布证据

## 1. 结论

`v0.7.0` 已从受保护的 `main` 成功发布。版本标签、提交标签、不可变摘要、容器烟雾测试和 SLSA v1 构建来源证明均验证通过。

发布物：

```text
ghcr.io/wayne62314/ai-agent-orchestrator:0.7.0
ghcr.io/wayne62314/ai-agent-orchestrator:sha-7213c87ed7e12a9969e23d8a7502fe1f1efd95e7
ghcr.io/wayne62314/ai-agent-orchestrator@sha256:7495bb77cfeb1bd7e09afff369ff12e64ab97d40c5809987b4de6f9e24e70340
```

生产部署只应使用最后一个按摘要固定的地址。

## 2. 合并和标签

| 项目 | 证据 |
| --- | --- |
| PR | `#7`，状态 `MERGED` |
| 合并提交 | `7213c87ed7e12a9969e23d8a7502fe1f1efd95e7` |
| PR CI | run `30514427979`，成功 |
| 合并后 main CI | run `30514703644`，成功 |
| 发布标签 | 注释标签 `v0.7.0` |
| 标签目标 | 与合并提交完全一致 |
| 包版本 | `0.7.0` |

标签创建前确认远端和本地均不存在同名标签，并等待合并后主分支 CI 全绿。

## 3. 发布工作流

发布 run `30514795952` 的 `Publish and attest` 作业全部通过：

1. 检出标签源码；
2. 验证标签格式、主分支归属和包版本；
3. 登录 GHCR；
4. 构建并推送两个不可变标签；
5. 生成并上传构建来源证明；
6. 按摘要重新拉取镜像；
7. 验证应用版本 `0.7.0`；
8. 验证容器用户为 `orchestrator`；
9. 从 Registry 注销。

## 4. 镜像标签和可见性

匿名 GHCR Registry 请求获得 pull token，并确认：

| 标签 | Registry 摘要 |
| --- | --- |
| `0.7.0` | `sha256:7495bb77cfeb1bd7e09afff369ff12e64ab97d40c5809987b4de6f9e24e70340` |
| `sha-7213c87ed7e12a9969e23d8a7502fe1f1efd95e7` | `sha256:7495bb77cfeb1bd7e09afff369ff12e64ab97d40c5809987b4de6f9e24e70340` |

两个标签指向同一镜像。GitHub 包页面当前显示为 Public，匿名 Registry 请求成功。发布过程中没有调用包可见性修改 API；这纠正了发布前“包将保持默认私有”的假设。

## 5. 来源证明

来源证明：

- GitHub attestation ID：`37885279`；
- Predicate：`https://slsa.dev/provenance/v1`；
- Subject：上述镜像名和摘要；
- Source ref：`refs/tags/v0.7.0`；
- Source commit：合并提交 `7213c87...`；
- Signer workflow：`.github/workflows/release-image.yml`；
- Runner：GitHub-hosted；
- OIDC issuer：GitHub Actions；
- Rekor transparency log 时间戳有效。

使用以下额外约束执行 `gh attestation verify`，退出码为零：

```text
--repo Wayne62314/ai-agent-orchestrator
--source-ref refs/tags/v0.7.0
--source-digest 7213c87ed7e12a9969e23d8a7502fe1f1efd95e7
--signer-workflow Wayne62314/ai-agent-orchestrator/.github/workflows/release-image.yml
--deny-self-hosted-runners
```

这不仅确认“存在一份证明”，还约束了来源仓库、标签、提交、签名工作流和 Runner 类型。

## 6. 已知边界

- 当前没有长期云平台凭据或服务器；
- 尚未部署稳定 HTTPS 域名；
- 尚未配置生产 Webhook、备份和监控；
- 当前仍是单实例 SQLite 架构；
- 镜像包已经公开，任何人都可以匿名拉取，但运行服务仍需要自行提供 Webhook 密钥和持久存储。

## 7. 下一步

选择一个支持以下能力的长期托管目标：

- 常驻容器进程；
- 持久卷；
- 稳定 HTTPS 域名；
- 环境变量密钥；
- 健康检查；
- 备份和监控。

选择后按已验证摘要部署，再执行一次真实 GitHub Webhook 生产验收。
