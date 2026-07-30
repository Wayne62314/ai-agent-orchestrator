# 阶段 6 第三部分：真实公网 Webhook 端到端演练

## 1. 目标

本部分验证的不再是进程内模拟请求，而是真实 GitHub `workflow_run` 事件经过公开 HTTPS 地址到达编排器，并且只在签名、仓库、工作流、分支和成功结论全部匹配时恢复持久任务。

演练使用临时 Cloudflare Quick Tunnel。它不要求新建云账号，适合开发验收，但官方不提供 SLA，地址会随进程重启变化，因此不作为生产部署。

## 2. 可重复操作面

版本更新为 `0.6.1`，新增：

```text
agent-orchestrator --db <database> demo prepare-ci \
  --repository OWNER/REPO \
  --workflow CI \
  --branch BRANCH \
  --workspace <path> \
  --timeout-seconds 1800

agent-orchestrator --db <database> demo verify-ci <task_id>
```

`prepare-ci` 持久化一个任务和精确的 CI 等待条件。`verify-ci` 必须同时看到以下证据才返回成功：

- 任务从 `WAITING_FOR_SIGNAL` 恢复为 `READY`；
- 等待状态为 `SATISFIED`；
- 外部事件收据为 `CONSUMED`；
- 事件已通过 HMAC-SHA256 认证；
- 任务审计哈希链有效。

## 3. 安全边界

- Webhook 密钥仅存在于服务进程环境和 GitHub Webhook 配置中；
- 报告、日志、数据库和命令参数不记录密钥；
- 服务只暴露 Webhook、健康和就绪入口；
- Quick Tunnel 结束后删除仓库 Webhook；
- 临时地址不写入长期配置，不宣称为生产服务。

## 4. 实现修正

接入演练命令时发现，阶段 6 第二部分添加 `serve` 分支后，`effect list`、`effect recover-stale` 和 `effect reconcile` 的处理代码被错误放在 `serve` 的返回语句后，因而不可达。本部分恢复了这些命令的正确分派位置，并纳入完整回归测试。

## 5. 本地验证

- `python -m unittest discover -s tests -v`：91 项通过；
- `python -m ruff check src tests`：通过；
- `git diff --check`：通过。

## 6. 真实演练结果

等待本次 PR 的真实 GitHub Actions 运行完成后填写：

- 临时 HTTPS 健康检查；
- GitHub Webhook ping；
- `workflow_run` 投递结果；
- 任务恢复证据；
- 临时资源清理结果。

## 7. 生产部署边界

本次演练证明公开网络链路和 GitHub 真实事件闭环，但不替用户选择会产生账号、费用或长期运维责任的云平台。生产部署仍需确定：

1. 托管平台或服务器；
2. 持久卷；
3. 稳定 HTTPS 域名；
4. 密钥管理和轮换；
5. 备份、监控和升级策略。
