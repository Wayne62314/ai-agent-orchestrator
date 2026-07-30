# 11 — 阶段 0 实验记录

## 实验信息

- 日期：2026-07-30
- 系统：Windows
- 工作模式：本机 Codex 桌面环境
- Python SDK：`openai-codex 0.144.4`
- SDK 自带 CLI：`codex-cli 0.144.4`
- 认证：ChatGPT 管理认证
- 原则：除最小恢复实验外均为只读；不记录账户数值和凭据

## E-01：桌面应用能否直接作为 CLI

### 步骤

1. 查找系统中的 `codex` 命令；
2. 系统解析到 WindowsApps 内的桌面应用可执行文件；
3. 尝试读取版本和帮助。

### 结果

- 命令路径可发现；
- 子进程执行返回 `Access is denied`；
- 不能把“桌面应用已安装”视作“Codex CLI 可用”。

### 结论

失败，但获得有效架构约束：安装检测必须区分 Desktop、独立 CLI 与 SDK 自带 runtime。

## E-02：官方 Python SDK 安装

### 步骤

1. 安装 `openai-codex` 到隔离实验目录；
2. 检查包版本；
3. 查找自带 `codex.exe`；
4. 运行 `--version`、`exec --help` 和 `app-server --help`。

### 结果

- SDK 安装成功；
- 同时安装 `openai-codex-cli-bin 0.144.4`；
- 自带 CLI 可以正常执行；
- `codex exec` 暴露 JSONL、output schema、sandbox 和 resume；
- `codex app-server` 支持 stdio。

### 结论

通过。SDK 自带 runtime 可以解决桌面应用 CLI 不可执行的问题。

## E-03：App Server 握手与认证状态

### 步骤

1. 以 stdio 启动 SDK 自带 App Server；
2. 发送 `initialize`；
3. 发送 `initialized`；
4. 调用 `account/read`；
5. 只输出是否成功和认证类型，不输出 token。

### 结果

- `initialize`：通过；
- `account/read`：通过；
- 认证存在：是；
- 认证类型：`chatgpt`；
- App Server 正常退出。

### 结论

通过。可在本机复用 ChatGPT 管理认证，无需读取或复制凭据。

## E-04：限额窗口读取

### 步骤

1. 在已初始化的 App Server 连接中调用 `account/rateLimits/read`；
2. 检查必要字段是否存在；
3. 不记录实际百分比和时间值。

### 结果

- 请求成功；
- `rateLimits` 存在；
- `usedPercent` 存在；
- `resetsAt` 存在。

### 结论

通过。额度恢复可以用官方窗口数据驱动，并以重新读取确认，而不是固定等待。

## E-05：SDK API 覆盖检查

### 步骤

检查已安装 SDK 的公开签名和类型。

### 结果

存在：

- `Codex.thread_start`
- `Codex.thread_resume`
- `Thread.run`
- `TurnHandle.interrupt`
- `Codex.thread_archive`

当前高层 API 未找到直接的 `rate_limits_read` 方法，但生成的协议类型包含：

- `account/rateLimits/read`
- `account/rateLimits/updated`
- `TurnInterrupt`
- `ThreadResume`

### 结论

SDK 适合执行生命周期；额度读取需要薄 JSON-RPC 层。

## E-06：关闭连接后的 thread 恢复

### 步骤

1. 使用 SDK 创建 read-only thread；
2. 运行一个不使用工具的固定响应任务；
3. 关闭第一个 SDK/App Server 连接；
4. 新建第二个 SDK/App Server 连接；
5. 用保存的 `thread_id` 调用 `thread_resume`；
6. 执行第二个固定响应任务；
7. 归档实验 thread。

### 结果

- thread 创建：通过；
- 第一 turn：通过；
- 新连接恢复：通过；
- 第二 turn：通过；
- 实验 thread：已归档。

### 结论

通过。最关键的“原进程结束后仍能恢复 Codex 上下文”已在本机实证。

## 未执行实验

### Turn 中断行为

原因：协议、SDK 高层方法和生成类型均已确认。为避免额外启动一个人为长时间模型任务，阶段 0 未做真实中断。

安排：阶段 1 用受控测试任务验证：

- interrupt 响应；
- `turn/completed.status == interrupted`；
- 中断后检查点；
- 再次恢复。

### 限额更新长连接通知

原因：需要等待真实额度变化，无法在短实验中可靠触发。

安排：

- 阶段 1 保持连接监听；
- 同时按 `resetsAt` 定时回查；
- 用合成事件测试状态转换。

### 真正触发额度耗尽

原因：不应为了测试主动消耗用户额度。

安排：

- 用模拟 RateLimitProvider 验证状态机；
- 在自然遇到真实限制时采集脱敏事件。

## 清理

- 恢复实验 thread 已归档；
- 没有修改用户代码仓库；
- SDK 与依赖保留在项目 `work` 临时目录，作为阶段 0 可重复验证环境；
- 用户交付目录只包含 Markdown 文档。

