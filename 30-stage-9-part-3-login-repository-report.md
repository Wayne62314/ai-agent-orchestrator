# 阶段九第三部分：Codex 登录与原生仓库选择

## 1. 结论

阶段九第三部分完成首次真实使用入口：桌面应用可以读取 Codex 账户状态，
启动 ChatGPT 浏览器登录、设备代码登录或 API Key 登录，并通过 Windows
原生文件夹对话框选择和检查 Git 仓库。

登录凭据仍由 Codex 管理。Orchestrator 不把 API Key、访问令牌或登录响应写入
SQLite、日志、Checkpoint 或备份。

## 2. Codex 账户流程

- 桌面 sidecar 与执行适配器共享同一个官方 `openai-codex` 客户端；
- `account/read` 只返回登录状态、账户类型、脱敏邮箱和套餐摘要；
- 浏览器登录只把 Codex 返回的 HTTPS 授权地址交给系统浏览器；
- 设备代码登录显示一次性代码，并打开 Codex 提供的验证地址；
- API Key 只在一次 RPC 调用中转交 Codex，返回值和错误中均不回显；
- 登录等待在后台线程完成，不阻塞 JSONL 请求循环；
- 支持查询、取消登录尝试和退出账户。

桌面 RPC 新增白名单方法：

- `account/read`
- `account/login/start`
- `account/login/status`
- `account/login/cancel`
- `account/logout`

## 3. 原生仓库选择

- 使用 Tauri 官方 Dialog 插件打开 Windows 原生文件夹选择器；
- 前端只获得用户明确选择的目录；
- `repository/inspect` 通过现有 `WorktreeService` 做只读检查；
- 返回规范化仓库根目录、当前分支、完整 HEAD、脏路径数量与最多 100 条摘要；
- 拒绝非 Git 目录、裸仓库和 detached HEAD；
- 未提交修改只做提示，不执行 stash、复制、提交或清理；
- 创建任务时仍会重新检查仓库，避免使用过期的预检结果。

## 4. 原生权限

Tauri 外壳只增加两个官方、最小化能力：

- 打开文件夹选择对话框；
- 打开经过前端 HTTPS 校验的登录 URL。

没有增加任意文件读取、shell、数据库查询或网络监听入口。

## 5. 验证

- Python 全量回归：133 项通过；
- 桌面账户与仓库边界：新增 4 项测试；
- React 用户旅程：4 项通过；
- React/TypeScript 生产构建：通过；
- Ruff：通过；
- Python sdist 与 wheel：通过；
- 本机真实 Codex `account/read`：通过；
- Cargo 格式与锁文件元数据：通过；
- Rust/Tauri 原生编译：由 Windows CI 验证。

## 6. 下一部分

阶段九第四部分处理长时间运行协调：

1. sidecar 内建立 Run 完成收集器和租约心跳；
2. 保证后台完成、暂停和取消只有一个最终结算者；
3. 应用重启后把过期 Run 转为带 Checkpoint 的显式恢复；
4. 将完成、额度等待和需要处理状态及时刷新到桌面界面；
5. 增加并发竞态、崩溃与重启恢复测试。
