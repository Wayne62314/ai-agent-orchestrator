# 阶段九第二部分：Tauri 原生外壳与真实任务控制

## 1. 结论

阶段九第二部分把第一部分的浏览器界面接入 Tauri 2 原生窗口，并把 UI 的
任务写操作接入阶段八应用服务。桌面主链仍不监听任何 HTTP 或 WebSocket 端口。

本部分不是安装包。Python 自包含 sidecar、签名安装程序与升级属于阶段十；
当前 Tauri 开发外壳使用本机 Python 模块，Windows CI 负责验证原生 Rust 边界。

## 2. 原生外壳

- Tauri 2 Rust 工程和固定版本 Cargo 锁文件；
- 1360 × 900 主窗口、最小尺寸和 CSP；
- 单一 `sidecar_request` 命令；
- `aiao.desktop.v1` 精确协议校验；
- 允许方法白名单，不提供 shell、SQL 或任意进程入口；
- 请求与响应各自限制为 1 MiB；
- 每个请求使用递增 id 并验证响应 id；
- sidecar 响应超时后终止进程，不自动重放写操作；
- Windows 下隐藏 Python 控制台窗口；
- 应用退出时终止所管理的 sidecar。

## 3. 真实应用服务

以下 RPC 已接入阶段八的 `TaskLifecycleService` 和 `ApprovalService`：

- `task/create`；
- `task/start`；
- `task/pause`；
- `task/resume`；
- `task/cancel`；
- `approval/decide`。

创建任务会校验 Git 仓库并建立隔离 Worktree。任务命令要求
`expectedVersion`，重复请求在目标状态已达成时安全返回，创建请求通过持久的
确定性任务 id 去重。审批决定继续绑定界面展示的完整动作哈希。

## 4. UI 数据模型

Python sidecar 现在直接返回界面使用的：

- 当前任务和最近任务；
- 稳定状态标签、进度和下一步；
- Worktree 仓库与分支；
- Checkpoint 和验收摘要；
- 脱敏活动记录；
- 待审批列表。

前端不读取 SQLite 行，也不能绕过应用服务直接修改数据库。

## 5. 验证

- 桌面命令与读取边界：9 项测试通过；
- 完整 Python 回归：129 项通过；
- React 用户旅程：3 项通过；
- React 生产构建：通过；
- Ruff：通过；
- Python sdist 与 wheel：通过；
- JSONL sidecar 子进程冒烟测试：通过；
- Rust 格式与 Cargo 锁文件：本机生成并校验；
- Rust 原生编译与测试：由 Windows CI 验证。

## 6. 本机环境

已安装 Rust 1.97.1。WebView2 已存在。本机尚缺 Microsoft C++ Build Tools
与 Windows SDK，因此本次不在本机生成原生可执行文件；安装大型构建组件不作为
运行最终产品的用户前置条件，阶段十的安装包必须自包含。

## 7. 下一部分

1. 接入真实 Codex 账户状态与登录流程；
2. 接入原生仓库文件夹选择器和仓库检查；
3. 建立运行完成、心跳与后台恢复协调器；
4. 完成活动、Checkpoint、验收与报告详情页；
5. 接入备份恢复、诊断导出和 Windows 本地通知；
6. 运行真实 Codex 的完整桌面端到端旅程。
