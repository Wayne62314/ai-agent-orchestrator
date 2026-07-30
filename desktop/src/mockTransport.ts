import type {
  ApprovalSummary,
  CreateTaskInput,
  SystemSnapshot,
  TaskState,
  TaskSummary,
} from "./types";

const active: TaskSummary = {
  id: "task_7f31c8",
  title: "为登录模块补充测试",
  objective: "补齐登录模块的异常路径测试并保证全部验收通过。",
  repository: "C:\\Projects\\Northstar",
  branch: "aiao/task-7f31c8",
  state: "RUNNING",
  version: 8,
  progress: 62,
  nextAction: "完成异常登录测试后运行自动验收",
  checkpointLabel: "2 分钟前已安全保存",
  verificationPassed: 3,
  verificationTotal: 4,
  updatedAt: "刚刚",
};

const approval: ApprovalSummary = {
  id: "approval_29ae",
  taskId: active.id,
  action: "创建本地 Git 提交",
  risk: "将 6 个已验收文件写入任务分支的本地历史；不会推送或合并。",
  expiresIn: "12 分钟",
  hash: "9e4c5a90…a21f",
};

let snapshot: SystemSnapshot = {
  protocol: "aiao.desktop.v1",
  appVersion: "0.9.0-dev",
  schemaVersion: 6,
  healthy: true,
  background: {
    running: true,
    trackedTaskId: active.id,
    heartbeatError: null,
  },
  account: {
    signedIn: true,
    accountType: "ChatGPT",
    email: "demo@example.invalid",
    planType: "Plus",
    requiresOpenaiAuth: true,
  },
  activeTask: active,
  recentTasks: [
    active,
    {
      ...active,
      id: "task_92a1d0",
      title: "整理搜索结果缓存",
      state: "SUCCEEDED",
      progress: 100,
      verificationPassed: 5,
      verificationTotal: 5,
      updatedAt: "昨天",
      nextAction: "查看交付报告",
    },
    {
      ...active,
      id: "task_104bf2",
      title: "升级依赖并修复类型错误",
      state: "PAUSED",
      progress: 44,
      verificationPassed: 0,
      verificationTotal: 3,
      updatedAt: "周一",
      nextAction: "从已验证 Checkpoint 恢复",
    },
  ],
  activities: [
    {
      id: "activity-1",
      title: "单元测试通过",
      detail: "3 / 4 项必选检查已通过",
      time: "14:31",
      tone: "success",
    },
    {
      id: "activity-2",
      title: "Codex 完成一轮修改",
      detail: "更新 4 个文件，等待最后一项检查",
      time: "14:30",
      tone: "active",
    },
    {
      id: "activity-3",
      title: "已从额度等待恢复",
      detail: "Worktree 与 Checkpoint 校验一致",
      time: "14:24",
      tone: "waiting",
    },
  ],
  approvals: [approval],
  backupLabel: "今天 02:00",
};

export async function mockRequest<T>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  await new Promise((resolve) => window.setTimeout(resolve, 80));
  if (method === "system/initialize" || method === "system/status") {
    return structuredClone(snapshot) as T;
  }
  if (method === "account/read") {
    return structuredClone(snapshot.account) as T;
  }
  if (method === "account/login/start") {
    snapshot = {
      ...snapshot,
      account: {
        signedIn: true,
        accountType: "ChatGPT",
        email: "demo@example.invalid",
        planType: "Plus",
        requiresOpenaiAuth: true,
      },
    };
    return {
      loginType: params.type,
      loginId: "mock-login",
      status: "SUCCEEDED",
      account: snapshot.account,
    } as T;
  }
  if (method === "account/logout") {
    snapshot = {
      ...snapshot,
      account: {
        signedIn: false,
        accountType: null,
        email: null,
        planType: null,
        requiresOpenaiAuth: true,
      },
    };
    return structuredClone(snapshot.account) as T;
  }
  if (method === "repository/inspect") {
    return {
      repository: params.path,
      branch: "main",
      headRevision: "a1c468a38d579eb46b83d986cf77ab544e0ac0aa",
      dirty: true,
      dirtyPaths: ["README.md", "src/example.ts"],
      dirtyPathCount: 2,
    } as T;
  }
  if (method === "task/create") {
    const input = params.input as CreateTaskInput;
    const created: TaskSummary = {
      id: `task_${Math.random().toString(16).slice(2, 8)}`,
      title: input.title,
      objective: input.objective,
      repository: input.repository,
      branch: "aiao/task-new",
      state: "READY",
      version: 1,
      progress: 8,
      nextAction: "确认后开始 Codex 任务",
      checkpointLabel: "初始状态已验证",
      verificationPassed: 0,
      verificationTotal: input.checks.length,
      updatedAt: "刚刚",
    };
    snapshot = {
      ...snapshot,
      activeTask: created,
      recentTasks: [created, ...snapshot.recentTasks],
    };
    return structuredClone(created) as T;
  }
  if (method.startsWith("task/")) {
    const task = snapshot.activeTask;
    if (!task) throw new Error("当前没有活动任务。");
    const target: Record<string, TaskState> = {
      "task/start": "RUNNING",
      "task/pause": "PAUSED",
      "task/resume": "RUNNING",
      "task/cancel": "CANCELLED",
    };
    const state = target[method];
    if (!state) throw new Error(`不支持的方法：${method}`);
    const updated = {
      ...task,
      state,
      version: task.version + 1,
      checkpointLabel:
        state === "PAUSED" ? "刚刚已验证安全保存" : task.checkpointLabel,
      nextAction:
        state === "PAUSED"
          ? "恢复后继续当前 Codex thread"
          : state === "CANCELLED"
            ? "检查保留的 Worktree"
            : task.nextAction,
    };
    snapshot = {
      ...snapshot,
      activeTask: updated,
      recentTasks: snapshot.recentTasks.map((item) =>
        item.id === updated.id ? updated : item,
      ),
    };
    return structuredClone(updated) as T;
  }
  if (method === "approval/decide") {
    snapshot = { ...snapshot, approvals: [] };
    return { decided: true } as T;
  }
  throw new Error(`Fake transport 未实现：${method}`);
}
