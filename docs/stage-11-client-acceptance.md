# 阶段十一客户端验收手册

## 目的

本手册用于证明同一个候选安装包能在真实 Windows 客户端上由普通用户安装、
登录、完成任务并卸载。恢复流程先要求 Windows 11 单机签收；Windows 11
通过后，再恢复 Windows 10 22H2 并形成双系统矩阵。CI hosted VM 证据仍然
有效，但不能代替客户端签收。

## 证据原则

- 两台客户端必须使用相同版本、完整 Git 提交和安装包 SHA-256。
- 每项检查初始状态都是 `not-tested`，不得根据 CI 或另一台电脑推定通过。
- `failed` 和 `blocked` 必须记录可复现说明；报告不得包含邮箱、令牌、API Key
  或原始认证输出。
- `passed` 只表示操作者在报告所列机器和候选包上亲自观察到结果。
- 完整矩阵必须同时包含 `windows-10-22h2` 和 `windows-11`，校验器会拒绝缺项。

## 每台机器的步骤

1. 从同一个已通过 CI 的提交下载候选安装包，并先核对文件来源。
2. 优先使用候选包内的 `START-WINDOWS11-ACCEPTANCE.cmd`。它会校验候选包，
   启动安装程序并逐项记录结果，不要求安装 Python。
3. 如果需要手工创建验收表，可用 Windows PowerShell：

   ```powershell
   .\packaging\new-windows-client-acceptance.ps1 `
     -InstallerPath <候选安装包> `
     -Commit <40 位完整提交> `
     -Version <版本> `
     -OutputPath <证据文件.json>
   ```

4. 逐项完成交互式安装、首次启动、Codex 登录、选择真实仓库和真实任务。
5. 特别验证启动无黑框、账户信息真实、字段无演示填充、仓库可更换、创建有反馈、
   无项目命令也能完成，以及三类验收证据没有混淆。
6. 在 200% 缩放下检查主要页面；只用键盘走完主要流程；检查本地通知。
7. 阅读卸载界面文字，执行卸载并确认用户数据默认保留。
8. 把每项状态改为 `passed`、`failed` 或 `blocked`，并写简短、去敏说明。
9. 维护者可校验 Windows 11 恢复记录：

   ```powershell
   python .\packaging\stage11_acceptance.py windows11-recovery <证据文件.json>
   ```

## 完整矩阵签核

Windows 11 恢复门禁通过、Windows 10 22H2 重新开放后，运行：

```powershell
python .\packaging\stage11_acceptance.py matrix `
  <windows-10-22h2.json> <windows-11.json>
```

只有校验返回“完整 Windows 10/11 验收矩阵”，才可关闭最终客户端矩阵项。

## 本阶段不接受的替代证据

- 仅有 GitHub Actions Windows runner 结果；
- 在 Windows 11 上把兼容模式写成 Windows 10 通过；
- 仅启动 sidecar 而未启动桌面应用；
- 使用示例数据代替真实仓库和真实任务；
- 未执行的项目预先标记为 `passed`；
- 包含账号、令牌或 API Key 的截图和日志。
