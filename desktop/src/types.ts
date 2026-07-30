export type TaskState =
  | "DRAFT"
  | "READY"
  | "RUNNING"
  | "PAUSED"
  | "WAITING_FOR_SIGNAL"
  | "WAITING_FOR_APPROVAL"
  | "VERIFYING"
  | "NEEDS_ATTENTION"
  | "SUCCEEDED"
  | "CANCELLED";

export type Page = "home" | "new-task" | "task" | "approvals" | "settings";

export interface AccountSummary {
  signedIn: boolean;
  accountType: string | null;
  planType: string | null;
}

export interface TaskSummary {
  id: string;
  title: string;
  objective: string;
  repository: string;
  branch: string;
  state: TaskState;
  version: number;
  progress: number;
  nextAction: string;
  checkpointLabel: string;
  verificationPassed: number;
  verificationTotal: number;
  updatedAt: string;
}

export interface ActivityItem {
  id: string;
  title: string;
  detail: string;
  time: string;
  tone: "success" | "active" | "waiting" | "neutral";
}

export interface ApprovalSummary {
  id: string;
  taskId: string;
  action: string;
  risk: string;
  expiresIn: string;
  hash: string;
}

export interface SystemSnapshot {
  protocol: "aiao.desktop.v1";
  appVersion: string;
  schemaVersion: number;
  healthy: boolean;
  account: AccountSummary;
  activeTask: TaskSummary | null;
  recentTasks: TaskSummary[];
  activities: ActivityItem[];
  approvals: ApprovalSummary[];
  backupLabel: string;
}

export interface CreateTaskInput {
  title: string;
  objective: string;
  repository: string;
  permission: "read-only" | "workspace-write";
  checks: string[];
  maxRepairs: number;
}
