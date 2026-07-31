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
  email: string | null;
  planType: string | null;
  requiresOpenaiAuth: boolean;
}

export interface LoginAttempt {
  loginType: "chatgpt" | "chatgptDeviceCode" | "apiKey";
  loginId: string | null;
  status: "PENDING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  authorizationUrl?: string;
  verificationUrl?: string;
  userCode?: string;
  error?: string;
  account?: AccountSummary;
}

export interface RepositoryInspection {
  repository: string;
  branch: string;
  headRevision: string;
  dirty: boolean;
  dirtyPaths: string[];
  dirtyPathCount: number;
  suggestedChecks: {
    command: string;
    source: string;
    label: string;
  }[];
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
  manualConfirmationPending?: boolean;
  updatedAt: string;
}

export interface ActivityItem {
  id: string;
  title: string;
  detail: string;
  time: string;
  tone: "success" | "active" | "waiting" | "neutral";
}

export type TaskDetailSection =
  | "activities"
  | "runs"
  | "checkpoints"
  | "verifications"
  | "report";

export interface ActivityDetail {
  id: string;
  sequence: number;
  runId: string | null;
  title: string;
  kind: string;
  detail: string;
  createdAt: string;
  tone: ActivityItem["tone"];
}

export interface RunDetail {
  id: string;
  attempt: number;
  engine: string;
  state: string;
  startedAt: string;
  heartbeatAt: string | null;
  endedAt: string | null;
  exitReason: string | null;
  resultSummary: string | null;
  inputCheckpointId: string | null;
}

export interface CheckpointDetail {
  id: string;
  sequence: number;
  runId: string | null;
  status: string;
  schemaVersion: number;
  workspaceRevision: string | null;
  payloadHash: string;
  createdAt: string;
  error: string | null;
}

export interface VerificationDetail {
  id: string;
  runId: string | null;
  attempt: number;
  name: string;
  required: boolean;
  status: string;
  command: string[];
  exitCode: number | null;
  timedOut: boolean;
  outputTruncated: boolean;
  durationMs: number;
  summary: string;
  startedAt: string;
  endedAt: string;
}

export type TaskDetailItem =
  | ActivityDetail
  | RunDetail
  | CheckpointDetail
  | VerificationDetail;

export interface TaskDetailPage {
  section: Exclude<TaskDetailSection, "report">;
  items: TaskDetailItem[];
  nextCursor: string | null;
}

export interface DeliveryReport {
  section: "report";
  taskId: string;
  title: string;
  objective: string;
  state: TaskState;
  auditChainValid: boolean;
  attempts: {
    attempt: number;
    passed: number;
    total: number;
    requiredPassed: boolean;
  }[];
  evidence: {
    ai: {
      status: string;
      source: string;
      summary: string;
      independent: boolean;
    } | null;
    commands: {
      configured: number;
      records: number;
      passed: number;
    };
    manual: {
      status: string;
      source: string;
    } | null;
  };
  outcome: string;
  final: boolean;
}

export interface ApprovalSummary {
  id: string;
  taskId: string;
  action: string;
  risk: string;
  expiresIn: string;
  hash: string;
}

export interface BackupSummary {
  id: string;
  createdAt: string;
  sizeBytes: number;
}

export interface MaintenanceSummary {
  backups: BackupSummary[];
  latestBackup: BackupSummary | null;
  restoreAvailable: boolean;
  backupRetention: number;
  createdBackupId?: string;
  restoredBackupId?: string;
  safetyBackupCreated?: boolean;
  restartRecommended?: boolean;
}

export interface DiagnosticExport {
  exported: boolean;
  fileName: string;
  path: string;
  containsSensitiveData: false;
}

export interface SystemSnapshot {
  protocol: "aiao.desktop.v1";
  appVersion: string;
  schemaVersion: number;
  healthy: boolean;
  background: {
    running: boolean;
    trackedTaskId: string | null;
    heartbeatError: string | null;
  };
  account: AccountSummary;
  activeTask: TaskSummary | null;
  recentTasks: TaskSummary[];
  activities: ActivityItem[];
  approvals: ApprovalSummary[];
  backupLabel: string;
  maintenance: MaintenanceSummary;
}

export interface CreateTaskInput {
  title: string;
  objective: string;
  repository: string;
  permission: "read-only" | "workspace-write";
  checks: string[];
  maxRepairs: number;
  manualConfirmation: boolean;
}
