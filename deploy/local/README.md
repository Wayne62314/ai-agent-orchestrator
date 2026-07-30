# Windows 本地部署手册

## 目标

这是当前默认部署方式。服务和 SQLite 数据只保存在本机，不需要 Docker、云服务器、域名或付费账号。

默认边界：

- 只监听 `127.0.0.1:8080`；
- 数据保存在仓库的 `.local-runtime/data/state.db`；
- 密钥保存在 `.local-runtime/secrets`，并限制为当前 Windows 用户访问；
- 日志保存在 `.local-runtime/logs`；
- 备份保存在 `.local-runtime/backups`；
- 不开放 Windows 防火墙端口；
- 不创建公网隧道。

电脑关机、睡眠或用户未登录时，服务不会继续工作。GitHub 无法直接向 localhost 投递 Webhook；以后如确实需要，可以单独增加受控隧道，但不是本地运行的前提。

## 一次性安装

在项目根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\local\install.ps1
```

安装脚本会：

1. 在 `.local-runtime/venv` 创建独立 Python 环境；
2. 安装当前项目；
3. 初始化 SQLite；
4. 生成随机 webhook 密钥；
5. 不修改系统自启动项。

如果希望每次登录 Windows 后自动启动，并在每天 03:00 自动备份：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\local\install.ps1 -RegisterStartup
```

计划任务使用当前用户和 `Limited` 权限运行，不申请管理员权限。

## 启动与停止

```powershell
powershell -ExecutionPolicy Bypass -File deploy\local\start.ps1
powershell -ExecutionPolicy Bypass -File deploy\local\status.ps1
powershell -ExecutionPolicy Bypass -File deploy\local\stop.ps1
```

启动成功后：

```text
http://127.0.0.1:8080/healthz
http://127.0.0.1:8080/readyz
```

若启动失败，查看 `.local-runtime/logs` 中当天的 stdout、stderr 和 launcher 日志。

## 备份

服务运行期间也可以执行一致性备份：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\local\backup.ps1
```

默认保留最近 30 份。每份备份都会通过 SQLite `integrity_check`，临时文件验证成功后才原子改名。

自定义保留数量：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\local\backup.ps1 -Keep 60
```

建议定期把 `.local-runtime/backups` 复制到另一块磁盘；与数据库放在同一块磁盘只能防误操作，不能防磁盘损坏。

## 恢复

恢复必须显式停止服务并确认替换：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\local\stop.ps1
powershell -ExecutionPolicy Bypass -File deploy\local\restore.ps1 `
  -Backup ".local-runtime\backups\state-<timestamp>.db" `
  -ConfirmReplace
powershell -ExecutionPolicy Bypass -File deploy\local\start.ps1
```

恢复工具会先验证所选备份，再把当前数据库保存到 `.local-runtime/data/pre-restore`，最后原子替换数据库。服务仍运行、缺少确认参数或备份损坏时都会拒绝恢复。

## 本地安全边界

- 不要提交 `.local-runtime`；
- 不要把密钥内容贴到 Issue、PR 或日志；
- 不要把监听地址改为 `0.0.0.0`，除非已经完成认证、TLS 和防火墙设计；
- 不要删除整个 `.local-runtime` 作为普通“重启”手段；
- 注册计划任务不会让程序在电脑关机时运行；
- 如需彻底移除自启动，使用 Windows 任务计划程序删除 `AI Agent Orchestrator Local` 和 `AI Agent Orchestrator Local Backup`，数据不会自动删除。
