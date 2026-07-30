# 17 — 阶段 6 第一部分：CI 与仓库保护报告

## 1. 目标

把阶段 0–5 的本地验证升级为 GitHub 上每次 PR 和 `main` 推送都会运行的持续集成门禁，并为后续分支保护提供一个稳定的必选检查名 `CI`。

## 2. GitHub Actions

工作流 `.github/workflows/ci.yml` 包含：

- Windows 与 Linux；
- Python 3.11 与 3.14；
- 全部单元和集成测试；
- Ruff 高置信度规则检查；
- sdist 与 wheel 构建；
- wheel 强制安装与版本导入验证；
- 汇总检查 `CI`，只有所有矩阵测试和质量任务成功时才通过。

工作流只授予 `contents: read`，并设置并发取消，避免同一分支的过期运行继续消耗资源。官方 Actions 使用完整提交 SHA 固定；Dependabot 每周检查 GitHub Actions 更新。

## 3. Ruff 基线

CI 启用以下规则：

```text
E4, E7, E9, F, I
```

它们覆盖导入错误、未定义名称、语法级错误和导入顺序。现有代码中的五处问题已清理。更强的风格和复杂度规则留待独立重构，避免在基础设施 PR 中混入大量无关改写。

## 4. 仓库保护

已确认 Actions 默认权限为只读，工作流不能批准 Pull Request。

GitHub API 确认原私有仓库方案不支持 branch protection 或 repository rulesets。经仓库所有者明确要求，仓库已改为公开，并对 `main` 启用：

- 必须通过 Pull Request；
- 必选状态检查：`CI`；
- 分支必须与最新 `main` 同步；
- 必须解决全部 Review 对话；
- 禁止 force push；
- 禁止删除分支；
- 当前单人维护阶段批准人数设为 0，增加协作者后改为 1。

## 5. 验收

- 本地全部测试通过；
- Ruff 配置范围内无错误；
- sdist 与 wheel 构建成功；
- wheel 隔离安装后版本为 `0.5.0`；
- GitHub PR 上稳定检查 `CI` 通过；
- Actions 权限保持最小化；
- 仓库可见性变更由所有者明确授权，`main` 分支保护已启用。
