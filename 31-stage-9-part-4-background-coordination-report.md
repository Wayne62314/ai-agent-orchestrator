# 阶段九第四部分：后台完成、心跳与重启恢复

## 1. 结论

阶段九第四部分把桌面端的“开始任务”从一次状态写操作升级为真正的后台执行：
UI 返回后，Python sidecar 会持续收集 Codex Run、续租、执行自动验收，并把最终
状态持久化。用户不需要保持某个请求或页面处于等待状态。

本部分最重要的约束是：

> 每个 Run 只有一个结算者。

正常完成、暂停、取消和进程故障不会竞争写入两套互相矛盾的结果。

## 2. 单一结算者

`DesktopRunCoordinator` 为每个活动任务创建：

- 一个完成收集线程；
- 一个心跳线程；
- 一个持久恢复扫描器；
- 一份受锁保护的结算意图。

后台收集线程是唯一能够调用 `settle_result` 的路径。暂停和取消只登记意图并调用
非阻塞 `request_interrupt`，等待收集线程取得终态后统一：

- 完成 Run；
- 写入 Checkpoint；
- 应用状态机事件；
- 执行 Verification Policy；
- 释放或保留单活动任务租约。

如果 Codex 已经真实完成，而暂停请求同时到达，完成结果优先，避免把已完成工作
错误标记为暂停。

## 3. 适配器边界

执行适配器新增 `request_interrupt`：

- 只请求中断，不消费终态；
- `interrupt` 保留原有同步语义，并复用 `request_interrupt + collect`；
- Fake、Codex SDK 和 Codex App Server 适配器保持一致；
- 原有阶段二至阶段八调用方式不变。

`ExecutionCoordinator` 新增分离的：

- `await_result`
- `request_interrupt`
- `finish_result`

因此桌面后台可以先取得唯一结果，再依据受锁保护的意图完成一次持久结算。

## 4. 心跳与恢复

- 活动任务按固定间隔续租 Run 和单活动任务槽位；
- 心跳错误只暴露脱敏摘要，不会伪造成功；
- sidecar 启动后持续扫描过期 Run；
- 过期 Run 被标记为 abandoned；
- 对应任务进入 `NEEDS_ATTENTION`；
- 自动生成 `process_restart` Checkpoint；
- 清除旧进程留下的内存 Run 引用；
- UI 每 2.5 秒从持久状态刷新，不依赖内存事件。

应用退出时不安装 Windows Service，也不会在退出路径无限等待活动 Run。未完成 Run
依靠租约过期和 Checkpoint 恢复契约重新进入可处理状态。

## 5. 验证

新增后台协调测试覆盖：

- 正常完成、自动验收和活动槽释放；
- Run 与活动任务租约续期；
- 暂停的单次结算和单个 Checkpoint；
- 取消的单次结算和活动槽释放；
- 完成与暂停竞态时完成结果优先；
- 进程失联后自动进入 `NEEDS_ATTENTION` 并生成恢复 Checkpoint。

全量测试和原生 CI 结果记录在本部分 PR。

## 6. 下一部分

阶段九第五部分进入任务详情体验：

1. 活动、Run、Checkpoint、Verification 和交付报告详情；
2. 真实分页与刷新；
3. 错误、空状态和恢复引导；
4. 审批后的详情同步；
5. 为备份恢复、诊断导出和本地通知准备 UI 入口。
