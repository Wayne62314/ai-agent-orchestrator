import type {
  ApprovalSummary,
  CreateTaskInput,
  DeliveryReport,
  SystemSnapshot,
  TaskDetailItem,
  TaskDetailPage,
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
  appVersion: "0.12.1",
  schemaVersion: 7,
  healthy: true,
  background: {
    running: true,
    trackedTaskId: active.id,
    heartbeatError: null,
  },
  account: {
    signedIn: true,
    accountType: "ChatGPT",
    email: "user@example.invalid",
    planType: "free",
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
  maintenance: {
    backups: [
      {
        id: "state-20260730T020000000000Z.db",
        createdAt: "2026-07-30T02:00:00+08:00",
        sizeBytes: 245760,
      },
    ],
    latestBackup: {
      id: "state-20260730T020000000000Z.db",
      createdAt: "2026-07-30T02:00:00+08:00",
      sizeBytes: 245760,
    },
    restoreAvailable: true,
    backupRetention: 30,
  },
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
        email: "user@example.invalid",
        planType: "free",
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
      suggestedChecks: [
        {
          command: "npm test",
          source: "package.json → scripts.test",
          label: "运行前端测试",
        },
      ],
    } as T;
  }
  if (method === "maintenance/backup") {
    const created = {
      id: `state-${Date.now()}.db`,
      createdAt: new Date().toISOString(),
      sizeBytes: 262144,
    };
    snapshot = {
      ...snapshot,
      backupLabel: created.createdAt,
      maintenance: {
        ...snapshot.maintenance,
        backups: [created, ...snapshot.maintenance.backups],
        latestBackup: created,
        restoreAvailable: true,
        createdBackupId: created.id,
      },
    };
    return structuredClone(snapshot.maintenance) as T;
  }
  if (method === "maintenance/restore") {
    if (params.confirmation !== "RESTORE_BACKUP") {
      throw new Error("恢复确认不匹配。");
    }
    return {
      ...structuredClone(snapshot.maintenance),
      restoredBackupId: params.backupId,
      safetyBackupCreated: true,
      restartRecommended: true,
    } as T;
  }
  if (method === "maintenance/diagnostics") {
    return {
      exported: true,
      fileName: "diagnostics-demo.zip",
      path: "C:\\Users\\Demo\\AppData\\diagnostics\\diagnostics-demo.zip",
      containsSensitiveData: false,
    } as T;
  }
  if (method === "task/create") {
    const input = params.input as CreateTaskInput;
    const repository =
      input.repositoryMode === "new"
        ? `${input.projectParent}\\${input.projectName}`
        : input.repository;
    const created: TaskSummary = {
      id: `task_${Math.random().toString(16).slice(2, 8)}`,
      title: input.title,
      objective: input.objective,
      repository,
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
  if (method === "task/codex-thread") {
    return {
      taskId: params.taskId,
      threadId: "019fb779-5e2b-7d32-b7c8-b82e008da14b",
    } as T;
  }
  if (method === "task/detail") {
    const section = String(params.section);
    if (section === "report") {
      const report: DeliveryReport = {
        section: "report",
        taskId: active.id,
        title: active.title,
        objective: active.objective,
        state: snapshot.activeTask?.state ?? active.state,
        auditChainValid: true,
        attempts: [
          { attempt: 2, passed: 3, total: 4, requiredPassed: false },
          { attempt: 1, passed: 2, total: 4, requiredPassed: false },
        ],
        evidence: {
          ai: {
            status: "PASSED",
            source: "codex-self-review",
            summary: "Codex 已依据任务目标复核当前实现。",
            independent: false,
          },
          commands: {
            configured: 4,
            records: 8,
            passed: 5,
          },
          manual: null,
        },
        outcome: "任务尚未形成最终交付结论。",
        final: false,
      };
      return structuredClone(report) as T;
    }
    const items = {
      activities: snapshot.activities.map((item, index) => ({
        ...item,
        sequence: 30 - index,
        runId: "run_02",
        kind: index === 0 ? "VERIFICATION_RECORDED" : "RUN_FINISHED",
        createdAt: item.time,
      })),
      runs: [
        {
          id: "run_02",
          attempt: 2,
          engine: "codex-app-server",
          state: "RUNNING",
          startedAt: "2026-07-30T14:24:00+08:00",
          heartbeatAt: "2026-07-30T14:31:00+08:00",
          endedAt: null,
          exitReason: null,
          resultSummary: null,
          inputCheckpointId: "checkpoint_01",
        },
        {
          id: "run_01",
          attempt: 1,
          engine: "codex-app-server",
          state: "INTERRUPTED",
          startedAt: "2026-07-30T14:10:00+08:00",
          heartbeatAt: "2026-07-30T14:22:00+08:00",
          endedAt: "2026-07-30T14:23:00+08:00",
          exitReason: "usage_limit",
          resultSummary: "已安全保存，等待额度恢复。",
          inputCheckpointId: null,
        },
      ],
      checkpoints: [
        {
          id: "checkpoint_01",
          sequence: 1,
          runId: "run_01",
          status: "READY",
          schemaVersion: 1,
          workspaceRevision: "a1c468a3",
          payloadHash: "4a6d30b2f8d1",
          createdAt: "2026-07-30T14:23:00+08:00",
          error: null,
        },
      ],
      verifications: [
        {
          id: "verification_04",
          runId: "run_02",
          attempt: 2,
          name: "单元测试",
          required: true,
          status: "PASSED",
          command: ["python", "-m", "unittest"],
          exitCode: 0,
          timedOut: false,
          outputTruncated: false,
          durationMs: 1830,
          summary: "全部测试通过。",
          startedAt: "2026-07-30T14:30:00+08:00",
          endedAt: "2026-07-30T14:30:02+08:00",
        },
      ],
    } as const;
    const page: TaskDetailPage = {
      section: section as TaskDetailPage["section"],
      items: structuredClone([
        ...(items[section as keyof typeof items] ?? []),
      ]) as TaskDetailItem[],
      nextCursor: null,
    };
    return page as T;
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
