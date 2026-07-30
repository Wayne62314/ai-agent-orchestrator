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

2026-07-30 完成真实公网演练：

| 证据 | 结果 |
| --- | --- |
| 本地 `/readyz` | `200`，状态为 `ready` |
| 临时 HTTPS `/readyz` | `200`，状态为 `ready` |
| GitHub Webhook ping | `200 OK` |
| GitHub Actions run | `30512981996`，结论 `success` |
| `workflow_run` requested/in_progress | `202`，不满足成功条件，未恢复任务 |
| `workflow_run` completed | `200`，唯一活动等待被满足 |
| 任务状态 | `WAITING_FOR_SIGNAL → READY` |
| 等待状态 | `ACTIVE → SATISFIED` |
| 事件收据 | `CONSUMED`、`authenticated=true` |
| 内容信任级别 | `TRUSTED_METADATA` |
| 审计链 | 有效 |

持久化事件事实只包含允许的元数据：

```json
{
  "action": "completed",
  "branch": "agent/stage-6-public-webhook-demo",
  "conclusion": "success",
  "run_id": 30512981996,
  "status": "completed",
  "workflow": "CI"
}
```

GitHub Actions 的 Linux、Windows、Python 3.11、Python 3.14、质量、打包、wheel 安装、Docker 构建和稳定汇总检查全部通过。

## 7. 临时资源清理

- 仓库测试 Webhook 已删除，按 ID 复查结果为零；
- Cloudflare Quick Tunnel 进程已停止；
- 本地 Webhook 服务进程已停止；
- 随机 Webhook 密钥已从运行环境释放；
- 临时公网地址未写入仓库配置。

## 8. 生产部署边界

本次演练证明公开网络链路和 GitHub 真实事件闭环，但不替用户选择会产生账号、费用或长期运维责任的云平台。生产部署仍需确定：

1. 托管平台或服务器；
2. 持久卷；
3. 稳定 HTTPS 域名；
4. 密钥管理和轮换；
5. 备份、监控和升级策略。
