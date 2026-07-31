import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  CirclePause,
  CirclePlay,
  Clock3,
  Code2,
  FileCheck2,
  FolderGit2,
  Gauge,
  Home,
  KeyRound,
  LayoutDashboard,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Settings,
  ShieldCheck,
  Sparkles,
  Square,
  TerminalSquare,
  TriangleAlert,
} from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import {
  attachCodexWindow,
  chooseProjectParentFolder,
  chooseRepositoryFolder,
  desktopRequest,
  detachCodexWindow,
  openCodexThread,
  openTrustedLoginUrl,
  pollCodexDock,
  sendLocalNotification,
} from "./bridge";
import type {
  AccountSummary,
  ActivityDetail,
  ApprovalSummary,
  CheckpointDetail,
  CreateTaskInput,
  DiagnosticExport,
  DeliveryReport,
  LoginAttempt,
  MaintenanceSummary,
  Page,
  RepositoryInspection,
  RunDetail,
  SystemSnapshot,
  TaskDetailItem,
  TaskDetailPage,
  TaskDetailSection,
  TaskState,
  TaskSummary,
  VerificationDetail,
} from "./types";

const stateLabels: Record<TaskState, string> = {
  DRAFT: "设置任务",
  READY: "可以开始",
  RUNNING: "Codex 正在工作",
  PAUSED: "已安全暂停",
  WAITING_FOR_SIGNAL: "等待恢复条件",
  WAITING_FOR_APPROVAL: "等待你的批准",
  VERIFYING: "正在自动验收",
  NEEDS_ATTENTION: "需要处理",
  SUCCEEDED: "已完成",
  CANCELLED: "已取消",
};

function errorMessage(reason: unknown, fallback: string): string {
  if (reason instanceof Error && reason.message.trim()) return reason.message;
  if (typeof reason === "string" && reason.trim()) return reason;
  if (
    reason &&
    typeof reason === "object" &&
    "message" in reason &&
    typeof reason.message === "string" &&
    reason.message.trim()
  ) {
    return reason.message;
  }
  return fallback;
}

function taskActivityHeadline(state: TaskState): string {
  return {
    DRAFT: "正在准备任务",
    READY: "任务尚未开始",
    RUNNING: "Codex 正在处理任务",
    PAUSED: "任务已暂停",
    WAITING_FOR_SIGNAL: "任务正在等待恢复",
    WAITING_FOR_APPROVAL: "任务正在等待批准",
    VERIFYING: "正在检查任务结果",
    NEEDS_ATTENTION: "任务需要你的处理",
    SUCCEEDED: "任务已经完成",
    CANCELLED: "任务已经取消",
  }[state];
}

function App() {
  const [snapshot, setSnapshot] = useState<SystemSnapshot | null>(null);
  const [page, setPage] = useState<Page>("home");
  const [busy, setBusy] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [onboarding, setOnboarding] = useState(
    () => localStorage.getItem("aiao.onboarding") !== "complete",
  );
  const [notificationsEnabled, setNotificationsEnabled] = useState(
    () => localStorage.getItem("aiao.notifications") === "enabled",
  );
  const previousTaskState = useRef<TaskState | null>(null);
  const refreshInFlight = useRef(false);
  const actionInFlight = useRef(false);

  const refresh = async (force = false) => {
    if (refreshInFlight.current || (!force && actionInFlight.current)) return;
    refreshInFlight.current = true;
    setError("");
    try {
      setSnapshot(await desktopRequest<SystemSnapshot>("system/initialize"));
    } catch (reason) {
      setError(errorMessage(reason, "后台连接失败。"));
    } finally {
      refreshInFlight.current = false;
    }
  };

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const task = snapshot?.activeTask;
    const previous = previousTaskState.current;
    previousTaskState.current = task?.state ?? null;
    if (
      !task ||
      !previous ||
      previous === task.state ||
      !notificationsEnabled ||
      !["WAITING_FOR_APPROVAL", "NEEDS_ATTENTION", "SUCCEEDED"].includes(
        task.state,
      )
    ) {
      return;
    }
    void sendLocalNotification(
      task.state === "SUCCEEDED" ? "任务已完成" : "任务需要你的处理",
      `${task.title}：${task.nextAction}`,
    );
  }, [snapshot?.activeTask?.state, notificationsEnabled]);

  const runTaskAction = async (
    method: string,
    extra: Record<string, unknown> = {},
  ) => {
    const task = snapshot?.activeTask;
    if (!task) return;
    actionInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await desktopRequest<TaskSummary>(method, {
        taskId: task.id,
        expectedVersion: task.version,
        idempotencyKey: crypto.randomUUID(),
        ...extra,
      });
      await refresh(true);
    } catch (reason) {
      setError(errorMessage(reason, "操作没有完成。"));
    } finally {
      actionInFlight.current = false;
      setBusy(false);
    }
  };

  const selectedTask =
    snapshot?.recentTasks.find((task) => task.id === selectedTaskId) ??
    snapshot?.activeTask ??
    snapshot?.recentTasks[0] ??
    null;

  if (!snapshot) {
    return (
      <main className="boot-screen">
        <div className="brand-mark large">
          <Sparkles aria-hidden="true" />
        </div>
        <p className="eyebrow">AI Agent Orchestrator</p>
        <h1>{error ? "后台暂时不可用" : "正在恢复安全状态"}</h1>
        <p>{error || "校验数据库、工作区和 Codex 连接…"}</p>
        {error && (
          <button className="button primary" onClick={() => void refresh()}>
            <RefreshCw size={17} /> 重试
          </button>
        )}
      </main>
    );
  }

  if (onboarding) {
    return (
      <Onboarding
        snapshot={snapshot}
        onRefresh={refresh}
        onComplete={() => {
          localStorage.setItem("aiao.onboarding", "complete");
          setOnboarding(false);
        }}
      />
    );
  }

  return (
    <div className={`app-shell product-dock-shell${page === "home" || page === "task" ? " workspace-mode" : ""}`}>
      <TaskSidebar
        page={page}
        tasks={snapshot.recentTasks}
        selectedTaskId={selectedTask?.id ?? null}
        approvalCount={snapshot.approvals.length}
        onNavigate={setPage}
        onSelectTask={(taskId) => {
          setSelectedTaskId(taskId);
          setPage("task");
        }}
      />
      <div className="main-column">
        <Topbar snapshot={snapshot} />
        {error && (
          <div className="error-banner" role="alert">
            <TriangleAlert size={18} />
            <div>
              <strong>操作未完成</strong>
              <span>{error} 已有数据保持不变。</span>
            </div>
            <button aria-label="关闭错误提示" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}
        <main className="content" id="main-content">
          {(page === "home" || page === "task") && (
            <CodexWorkspace
              task={selectedTask}
              onNewTask={() => setPage("new-task")}
            />
          )}
          {page === "new-task" && (
            <NewTask
              onCancel={() => setPage("home")}
              onCreated={async () => {
                setSelectedTaskId(null);
                await refresh();
                setPage("task");
              }}
            />
          )}
          {page === "approvals" && (
            <Approvals
              approvals={snapshot.approvals}
              onDecided={refresh}
            />
          )}
          {page === "settings" && (
            <SettingsPage
              snapshot={snapshot}
              notificationsEnabled={notificationsEnabled}
              onNotificationsChanged={setNotificationsEnabled}
              onRefresh={refresh}
            />
          )}
        </main>
      </div>
      <MessageQueue approvals={snapshot.approvals} onOpen={() => setPage("approvals")} />
    </div>
  );
}

function TaskSidebar({
  page,
  tasks,
  selectedTaskId,
  approvalCount,
  onNavigate,
  onSelectTask,
}: {
  page: Page;
  tasks: TaskSummary[];
  selectedTaskId: string | null;
  approvalCount: number;
  onNavigate: (page: Page) => void;
  onSelectTask: (taskId: string) => void;
}) {
  return (
    <aside className="sidebar task-sidebar" aria-label="任务导航">
      <div className="brand">
        <div className="brand-mark"><Sparkles aria-hidden="true" /></div>
        <div><strong>Agent Dock</strong><span>Codex workspace</span></div>
      </div>
      <button className="new-task-button" onClick={() => onNavigate("new-task")}>
        <Plus size={18} /> 新建任务
      </button>
      <p className="task-list-label">任务</p>
      <div className="dock-task-list">
        {tasks.length ? tasks.map((task) => (
          <button
            key={task.id}
            className={selectedTaskId === task.id && (page === "home" || page === "task") ? "dock-task active" : "dock-task"}
            onClick={() => onSelectTask(task.id)}
          >
            <Bot size={17} />
            <span><strong>{task.title}</strong><small>{stateLabels[task.state]}</small></span>
          </button>
        )) : <p className="dock-task-empty">还没有任务</p>}
      </div>
      <div className="sidebar-footer">
        <button className={page === "approvals" ? "nav-item active" : "nav-item"} onClick={() => onNavigate("approvals")}>
          <ShieldCheck size={19} /><span>审批中心</span>
          {!!approvalCount && <b>{approvalCount}</b>}
        </button>
        <button className={page === "settings" ? "nav-item active" : "nav-item"} onClick={() => onNavigate("settings")}>
          <Settings size={19} /><span>设置</span>
        </button>
        <div className="local-note"><span className="status-dot" /><div><strong>仅在本机运行</strong><span>Codex 官方窗口</span></div></div>
      </div>
    </aside>
  );
}

function CodexWorkspace({
  task,
  onNewTask,
}: {
  task: TaskSummary | null;
  onNewTask: () => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const attaching = useRef(false);
  const [dock, setDock] = useState({ found: false, attached: false, near: false, leftButtonDown: false, dropReady: false });
  const [dockError, setDockError] = useState("");

  const hostRect = () => {
    const bounds = host.current?.getBoundingClientRect();
    if (!bounds) return null;
    const scale = window.devicePixelRatio || 1;
    const protrusion = Math.min(260, bounds.height * 0.8);
    return {
      x: Math.round(bounds.left * scale),
      y: Math.round((bounds.top - protrusion) * scale),
      width: Math.round(bounds.width * scale),
      height: Math.round((bounds.height + protrusion) * scale),
    };
  };

  const openTask = async () => {
    if (!task) return;
    setDockError("");
    try {
      const result = await desktopRequest<{ threadId: string }>("task/codex-thread", { taskId: task.id });
      await openCodexThread(result.threadId);
    } catch (reason) {
      setDockError(errorMessage(reason, "无法打开 Codex 任务。"));
    }
  };

  useEffect(() => {
    if (task) void openTask();
  }, [task?.id]);

  useEffect(() => {
    let disposed = false;
    const timer = window.setInterval(async () => {
      const rect = hostRect();
      if (!rect || attaching.current) return;
      try {
        const current = await pollCodexDock(rect);
        if (disposed) return;
        setDock(current);
        if (current.dropReady) {
          attaching.current = true;
          try {
            await attachCodexWindow(rect);
          } finally {
            attaching.current = false;
          }
        }
      } catch (reason) {
        if (!disposed) setDockError(errorMessage(reason, "无法读取 Codex 窗口状态。"));
      }
    }, 90);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      void detachCodexWindow();
    };
  }, []);

  if (!task) {
    return (
      <section className="dock-empty-product">
        <div className="brand-mark large"><Sparkles /></div>
        <h1>从一个任务开始</h1>
        <p>创建任务后，把官方 Codex 窗口拖入中央工作区。</p>
        <button className="button primary" onClick={onNewTask}><Plus size={18} /> 新建任务</button>
      </section>
    );
  }

  return (
    <section className="codex-workspace">
      <div className={`codex-frame${dock.near ? " magnet-preview" : ""}${dock.attached ? " attached" : ""}`}>
        <div className="codex-host" ref={host}>
          {!dock.attached && (
            <div className="codex-host-empty">
              <Bot size={34} />
              <strong>{dock.near ? "松开鼠标，吸附 Codex" : "把官方 Codex 窗口拖到中央插槽"}</strong>
              <span>它仍是你安装的真实 Codex，不是仿制界面</span>
              <div className="button-row">
                <button className="button primary" onClick={() => void openTask()}>在 Codex 中打开</button>
              </div>
            </div>
          )}
          {dock.near && <div className="magnet-glass"><span>释放以吸附</span></div>}
        </div>
      </div>
      <div className="dock-control-tray">
        <div className="dock-task-identity">
          <span>当前任务</span>
          <h1>{task.title}</h1>
          <small>Codex 的上半部分位于 Agent Dock 之外，下半部分与任务底座吸附。</small>
        </div>
        <div className="dock-tray-actions">
          <span className={dock.attached ? "dock-status attached" : "dock-status"}>
            {dock.attached ? "已吸附" : dock.near ? "释放以吸附" : dock.found ? "拖动 Codex 到这里" : "等待 Codex"}
          </span>
          {dock.attached && (
            <button className="button secondary" onClick={() => void detachCodexWindow()}>移出窗口</button>
          )}
        </div>
        {dockError && <div className="dock-inline-error" role="alert">{dockError}</div>}
      </div>
    </section>
  );
}

function MessageQueue({
  approvals,
  onOpen,
}: {
  approvals: ApprovalSummary[];
  onOpen: () => void;
}) {
  return (
    <aside className="message-queue">
      <header><p className="eyebrow">需要你处理</p><h2>消息队列</h2></header>
      {approvals.length ? approvals.map((approval) => (
        <button className="queue-card" key={approval.id} onClick={onOpen}>
          <TriangleAlert size={18} />
          <span><strong>{approval.action}</strong><small>{approval.risk}</small></span>
          <ChevronRight size={16} />
        </button>
      )) : (
        <div className="queue-empty"><CheckCircle2 size={24} /><strong>目前一切正常</strong><span>需要判断时会出现在这里</span></div>
      )}
    </aside>
  );
}

function Sidebar({
  page,
  approvalCount,
  onNavigate,
}: {
  page: Page;
  approvalCount: number;
  onNavigate: (page: Page) => void;
}) {
  const items: Array<{
    id: Page;
    label: string;
    icon: typeof Home;
    count?: number;
  }> = [
    { id: "home", label: "首页", icon: LayoutDashboard },
    { id: "new-task", label: "新建任务", icon: Plus },
    { id: "task", label: "当前任务", icon: Bot },
    {
      id: "approvals",
      label: "审批中心",
      icon: ShieldCheck,
      count: approvalCount,
    },
  ];
  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="brand">
        <div className="brand-mark">
          <Sparkles aria-hidden="true" />
        </div>
        <div>
          <strong>Orchestrator</strong>
          <span>Local agent control</span>
        </div>
      </div>
      <nav>
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              className={page === item.id ? "nav-item active" : "nav-item"}
              aria-current={page === item.id ? "page" : undefined}
              onClick={() => onNavigate(item.id)}
            >
              <Icon size={19} />
              <span>{item.label}</span>
              {!!item.count && <b>{item.count}</b>}
            </button>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <button
          className={page === "settings" ? "nav-item active" : "nav-item"}
          onClick={() => onNavigate("settings")}
        >
          <Settings size={19} />
          <span>设置与维护</span>
        </button>
        <div className="local-note">
          <span className="status-dot" />
          <div>
            <strong>仅在本机运行</strong>
            <span>无云端控制服务</span>
          </div>
        </div>
      </div>
    </aside>
  );
}

function Topbar({ snapshot }: { snapshot: SystemSnapshot }) {
  const accountLabel =
    snapshot.account.email || snapshot.account.accountType || "Codex 账户";
  return (
    <header className="topbar">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <div>
        <span className="system-pill">
          <span className="status-dot" />
          {snapshot.background.heartbeatError
            ? "后台需注意"
            : snapshot.background.running
              ? "后台运行中"
              : "后台正常"}
        </span>
        <span className="muted">Schema v{snapshot.schemaVersion}</span>
      </div>
      <div className="account">
        <div className="account-avatar">
          {accountLabel.slice(0, 1).toUpperCase()}
        </div>
        <div>
          <strong>{accountLabel}</strong>
          <span>{snapshot.account.signedIn ? "已连接" : "未登录"}</span>
        </div>
      </div>
    </header>
  );
}

function Dashboard({
  snapshot,
  busy,
  onNavigate,
  onTaskAction,
}: {
  snapshot: SystemSnapshot;
  busy: boolean;
  onNavigate: (page: Page) => void;
  onTaskAction: (method: string) => Promise<void>;
}) {
  const task = snapshot.activeTask;
  return (
    <div className="page-stack">
      <section className="hero-row">
        <div>
          <p className="eyebrow">今天的工作台</p>
          <h1>让任务继续，直到有证据地完成。</h1>
          <p className="lede">
            Codex 在隔离工作区中运行。你只需要处理真正需要判断的步骤。
          </p>
        </div>
        <button className="button primary" onClick={() => onNavigate("new-task")}>
          <Plus size={18} /> 新建任务
        </button>
      </section>

      {task ? (
        <section className="task-spotlight">
          <div className="spotlight-main">
            <div className="section-heading">
              <div>
                <p className="eyebrow">当前任务</p>
                <h2>{task.title}</h2>
              </div>
              <StateBadge state={task.state} />
            </div>
            <p className="task-objective">{task.objective}</p>
            <div className="progress-label">
              <span>任务进度</span>
              <strong>{task.progress}%</strong>
            </div>
            <div
              className="progress-track"
              role="progressbar"
              aria-valuenow={task.progress}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span style={{ width: `${task.progress}%` }} />
            </div>
            <div className="next-action">
              <Sparkles size={18} />
              <div>
                <span>下一步</span>
                <strong>{task.nextAction}</strong>
              </div>
            </div>
            <div className="button-row">
              <button className="button primary" onClick={() => onNavigate("task")}>
                打开任务 <ArrowRight size={17} />
              </button>
              {task.state === "RUNNING" && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void onTaskAction("task/pause")}
                >
                  <Pause size={17} /> 安全暂停
                </button>
              )}
              {task.state === "PAUSED" && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void onTaskAction("task/resume")}
                >
                  <Play size={17} /> 恢复
                </button>
              )}
            </div>
          </div>
          <div className="spotlight-aside">
            <Metric
              icon={FileCheck2}
              label="自动验收"
              value={`${task.verificationPassed} / ${task.verificationTotal}`}
              note="必选检查"
            />
            <Metric
              icon={ShieldCheck}
              label="Checkpoint"
              value="已验证"
              note={task.checkpointLabel}
            />
            <Metric
              icon={FolderGit2}
              label="隔离分支"
              value={task.branch.replace("aiao/task-", "")}
              note="原仓库未改动"
            />
          </div>
        </section>
      ) : (
        <section className="empty-card">
          <Bot size={32} />
          <h2>没有正在进行的任务</h2>
          <p>选择一个 Git 仓库，创建第一个可暂停、可恢复的 Codex 任务。</p>
          <button className="button primary" onClick={() => onNavigate("new-task")}>
            新建任务
          </button>
        </section>
      )}

      <div className="two-column">
        <section className="card">
          <div className="section-heading">
            <div>
              <p className="eyebrow">运行记录</p>
              <h2>最近活动</h2>
            </div>
            <button className="text-button" onClick={() => onNavigate("task")}>
              查看全部
            </button>
          </div>
          <ActivityList activities={snapshot.activities} />
        </section>
        <section className="card">
          <div className="section-heading">
            <div>
              <p className="eyebrow">本机健康</p>
              <h2>系统状态</h2>
            </div>
            <span className="health-score">4 / 4</span>
          </div>
          <ul className="health-list">
            <HealthRow label="Python 后台" value="正常" />
            <HealthRow label="Codex App Server" value="已登录" />
            <HealthRow label="活动 Checkpoint" value="已验证" />
            <HealthRow label="最近备份" value={snapshot.backupLabel} />
          </ul>
        </section>
      </div>

      <section className="card recent-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">本地历史</p>
            <h2>最近任务</h2>
          </div>
        </div>
        <div className="task-table" role="table" aria-label="最近任务">
          {snapshot.recentTasks.map((item) => (
            <button
              className="task-row"
              key={item.id}
              onClick={() => onNavigate("task")}
              role="row"
            >
              <span className="task-icon">
                <Code2 size={18} />
              </span>
              <span className="task-cell grow">
                <strong>{item.title}</strong>
                <small>{item.branch}</small>
              </span>
              <StateBadge state={item.state} compact />
              <span className="task-cell right">
                <strong>{item.progress}%</strong>
                <small>{item.updatedAt}</small>
              </span>
              <ChevronRight size={18} />
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function TaskDetail({
  task,
  activities,
  busy,
  onAction,
}: {
  task: TaskSummary;
  activities: SystemSnapshot["activities"];
  busy: boolean;
  onAction: (
    method: string,
    extra?: Record<string, unknown>,
  ) => Promise<void>;
}) {
  const [tab, setTab] = useState("概览");
  const [detailItems, setDetailItems] = useState<TaskDetailItem[]>([]);
  const [detailCursor, setDetailCursor] = useState<string | null>(null);
  const [report, setReport] = useState<DeliveryReport | null>(null);
  const [loadedSection, setLoadedSection] = useState<TaskDetailSection | null>(
    null,
  );
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const detailRequest = useRef(0);
  const tabs: { label: string; section: TaskDetailSection | null }[] = [
    { label: "概览", section: null },
    { label: "活动", section: "activities" },
    { label: "运行", section: "runs" },
    { label: "Checkpoint", section: "checkpoints" },
    { label: "验收", section: "verifications" },
    { label: "报告", section: "report" },
  ];
  const section = tabs.find((item) => item.label === tab)?.section ?? null;

  const loadDetail = async (
    target: TaskDetailSection,
    cursor: string | null = null,
    append = false,
  ) => {
    const request = ++detailRequest.current;
    setDetailLoading(true);
    setDetailError("");
    try {
      if (target === "report") {
        const loaded = await desktopRequest<DeliveryReport>("task/detail", {
          taskId: task.id,
          section: target,
        });
        if (request !== detailRequest.current) return;
        setReport(loaded);
        setDetailItems([]);
        setDetailCursor(null);
      } else {
        const page = await desktopRequest<TaskDetailPage>("task/detail", {
          taskId: task.id,
          section: target,
          limit: 20,
          ...(cursor ? { cursor } : {}),
        });
        if (request !== detailRequest.current) return;
        setDetailItems((current) =>
          append ? [...current, ...page.items] : page.items,
        );
        setDetailCursor(page.nextCursor);
        setReport(null);
      }
      setLoadedSection(target);
    } catch (reason) {
      if (request !== detailRequest.current) return;
      setDetailError(
        reason instanceof Error ? reason.message : "详情暂时无法读取。",
      );
    } finally {
      if (request === detailRequest.current) setDetailLoading(false);
    }
  };

  useEffect(() => {
    if (!section) return;
    setDetailItems([]);
    setDetailCursor(null);
    setReport(null);
    setLoadedSection(null);
    void loadDetail(section);
  }, [section, task.id, task.version]);

  return (
    <div className="page-stack">
      <section className="detail-header">
        <div>
          <p className="breadcrumb">任务 / {task.id}</p>
          <h1>{task.title}</h1>
          <p>{task.repository}</p>
        </div>
        <div className="detail-actions">
          <StateBadge state={task.state} />
          {task.state === "READY" && (
            <button
              className="button primary"
              disabled={busy}
              onClick={() => void onAction("task/start")}
            >
              <CirclePlay size={17} /> 开始任务
            </button>
          )}
          {task.state === "RUNNING" && (
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => void onAction("task/pause")}
            >
              <Pause size={17} /> 安全暂停
            </button>
          )}
          {task.state === "PAUSED" && (
            <button
              className="button primary"
              disabled={busy}
              onClick={() => void onAction("task/resume")}
            >
              <Play size={17} /> 恢复任务
            </button>
          )}
          {task.manualConfirmationPending && (
            <>
              <button
                className="button primary"
                disabled={busy}
                onClick={() => void onAction("task/confirm", { approved: true })}
              >
                <CheckCircle2 size={17} /> 确认完成
              </button>
              <button
                className="button secondary"
                disabled={busy}
                onClick={() =>
                  void onAction("task/confirm", { approved: false })
                }
              >
                需要继续修改
              </button>
            </>
          )}
          <button
            className="button danger-quiet"
            disabled={busy || task.state === "CANCELLED"}
            onClick={() => {
              if (
                window.confirm(
                  "确认取消任务？当前进度会先安全保存，任务 Worktree 将保留。",
                )
              ) {
                void onAction("task/cancel");
              }
            }}
          >
            <Square size={15} /> 取消
          </button>
        </div>
      </section>
      <div className="tabs" role="tablist" aria-label="任务详情">
        {tabs.map((item) => (
          <button
            role="tab"
            aria-selected={tab === item.label}
            className={tab === item.label ? "active" : ""}
            key={item.label}
            onClick={() => setTab(item.label)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "概览" && (
        <div className="detail-grid">
          <section className="card timeline-card">
            <div className="section-heading">
              <div>
                <p className="eyebrow">实时阶段</p>
                <h2>{stateLabels[task.state]}</h2>
              </div>
              <span className="live-label">
                <span /> 安全监控中
              </span>
            </div>
            <div className="agent-now">
              <div className="agent-orb">
                <Bot size={23} />
              </div>
              <div>
                <strong>{taskActivityHeadline(task.state)}</strong>
                <p>{task.nextAction}</p>
              </div>
            </div>
            <ActivityList activities={activities} />
          </section>
          <aside className="card facts-card">
            <h2>安全边界</h2>
            <Fact icon={FolderGit2} label="任务分支" value={task.branch} />
            <Fact icon={ShieldCheck} label="最近保存" value={task.checkpointLabel} />
            <Fact
              icon={FileCheck2}
              label="验收进度"
              value={`${task.verificationPassed} / ${task.verificationTotal} 通过`}
            />
            <Fact icon={KeyRound} label="权限" value="工作区写入 · 网络关闭" />
          </aside>
        </div>
      )}
      {tab !== "概览" && (
        <section className="card detail-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">持久化详情</p>
              <h2>{tab}</h2>
            </div>
            <button
              className="button quiet"
              disabled={detailLoading || !section}
              onClick={() => section && void loadDetail(section)}
            >
              <RefreshCw size={15} /> 刷新
            </button>
          </div>
          {detailError && (
            <div className="detail-error" role="alert">
              <TriangleAlert size={18} />
              <div>
                <strong>详情读取失败</strong>
                <span>{detailError} 已加载的数据没有改变。</span>
              </div>
            </div>
          )}
          {loadedSection !== section ||
          (detailLoading && detailItems.length === 0 && !report) ? (
            <DetailEmpty title="正在读取持久化证据" loading />
          ) : section === "report" ? (
            report ? (
              <ReportDetail report={report} />
            ) : (
              <DetailEmpty title="尚无可显示的交付报告" />
            )
          ) : detailItems.length ? (
            <DetailItems section={section!} items={detailItems} />
          ) : (
            <DetailEmpty title={`尚无${tab}记录`} />
          )}
          {detailCursor && section && section !== "report" && (
            <button
              className="button secondary load-more"
              disabled={detailLoading}
              onClick={() =>
                void loadDetail(section, detailCursor, true)
              }
            >
              {detailLoading ? "正在加载…" : "加载更早记录"}
            </button>
          )}
        </section>
      )}
    </div>
  );
}

function DetailItems({
  section,
  items,
}: {
  section: TaskDetailSection;
  items: TaskDetailItem[];
}) {
  if (section === "activities") {
    return (
      <ol className="evidence-list">
        {(items as ActivityDetail[]).map((item) => (
          <li key={item.id}>
            <div className={`evidence-status ${item.tone}`} />
            <div>
              <strong>{item.title}</strong>
              <p>{item.detail}</p>
              <small>
                #{item.sequence} · {formatTimestamp(item.createdAt)}
              </small>
            </div>
          </li>
        ))}
      </ol>
    );
  }
  if (section === "runs") {
    return (
      <div className="evidence-list cards">
        {(items as RunDetail[]).map((item) => (
          <article key={item.id}>
            <div className="evidence-row">
              <strong>第 {item.attempt} 次运行</strong>
              <EvidenceBadge value={item.state} />
            </div>
            <p>{item.resultSummary || "Codex 运行尚未形成结果摘要。"}</p>
            <small>
              {item.engine} · 开始于 {formatTimestamp(item.startedAt)}
            </small>
            {item.exitReason && <code>结束原因：{item.exitReason}</code>}
          </article>
        ))}
      </div>
    );
  }
  if (section === "checkpoints") {
    return (
      <div className="evidence-list cards">
        {(items as CheckpointDetail[]).map((item) => (
          <article key={item.id}>
            <div className="evidence-row">
              <strong>Checkpoint #{item.sequence}</strong>
              <EvidenceBadge value={item.status} />
            </div>
            <p>
              工作区版本：{item.workspaceRevision || "未记录"} · Schema v
              {item.schemaVersion}
            </p>
            <small>
              {formatTimestamp(item.createdAt)} · 摘要{" "}
              {item.payloadHash.slice(0, 12) || "待生成"}
            </small>
            {item.error && <code>{item.error}</code>}
          </article>
        ))}
      </div>
    );
  }
  return (
    <div className="evidence-list cards">
      {(items as VerificationDetail[]).map((item) => (
        <article key={item.id}>
          <div className="evidence-row">
            <strong>
              第 {item.attempt} 轮 · {item.name}
            </strong>
            <EvidenceBadge value={item.status} />
          </div>
          <p>{item.summary || "没有额外摘要。"}</p>
          <small>
            {item.required ? "必选检查" : "可选检查"} · {item.durationMs} ms ·
            退出码 {item.exitCode ?? "无"}
          </small>
          <code>{item.command.join(" ")}</code>
        </article>
      ))}
    </div>
  );
}

function ReportDetail({ report }: { report: DeliveryReport }) {
  return (
    <div className="report-detail">
      <div className="report-outcome">
        <FileCheck2 size={24} />
        <div>
          <strong>{report.final ? "最终交付结论" : "当前交付状态"}</strong>
          <p>{report.outcome}</p>
        </div>
      </div>
      <div className="report-facts">
        <Fact
          icon={ShieldCheck}
          label="审计链"
          value={report.auditChainValid ? "完整有效" : "需要检查"}
        />
        <Fact
          icon={FileCheck2}
          label="验收轮次"
          value={`${report.attempts.length} 轮`}
        />
      </div>
      <div className="evidence-list cards">
        <article>
          <div className="evidence-row">
            <strong>AI 判断</strong>
            <EvidenceBadge value={report.evidence.ai?.status ?? "未记录"} />
          </div>
          <p>
            {report.evidence.ai?.summary ||
              "尚未记录 AI 对目标和完成条件的判断。"}
          </p>
          <small>
            {report.evidence.ai
              ? "执行任务的 AI 自我复核，不是独立测试"
              : "没有 AI 证据"}
          </small>
        </article>
        <article>
          <div className="evidence-row">
            <strong>项目命令</strong>
            <EvidenceBadge
              value={
                report.evidence.commands.configured
                  ? `${report.evidence.commands.passed}/${report.evidence.commands.records}`
                  : "未配置"
              }
            />
          </div>
          <p>
            {report.evidence.commands.configured
              ? `配置了 ${report.evidence.commands.configured} 条命令；这里只统计真实执行结果。`
              : "没有配置项目命令，因此不能据此声称测试通过。"}
          </p>
        </article>
        <article>
          <div className="evidence-row">
            <strong>人工确认</strong>
            <EvidenceBadge value={report.evidence.manual?.status ?? "未要求"} />
          </div>
          <p>
            {report.evidence.manual
              ? "由桌面端用户明确确认或要求继续修改。"
              : "本任务未要求人工确认。"}
          </p>
        </article>
      </div>
      {report.attempts.length ? (
        <div className="check-list">
          {report.attempts.map((attempt) => (
            <HealthRow
              key={attempt.attempt}
              label={`第 ${attempt.attempt} 轮验收`}
              value={`${attempt.passed} / ${attempt.total} 通过`}
            />
          ))}
        </div>
      ) : (
        <p className="muted-copy">任务还没有产生验收记录。</p>
      )}
    </div>
  );
}

function DetailEmpty({
  title,
  loading = false,
}: {
  title: string;
  loading?: boolean;
}) {
  return (
    <div className="detail-empty">
      {loading ? <RefreshCw className="spin" size={25} /> : <TerminalSquare size={25} />}
      <strong>{title}</strong>
      <span>这里仅显示已持久化并完成脱敏的本地记录。</span>
    </div>
  );
}

function EvidenceBadge({ value }: { value: string }) {
  return <span className={`evidence-badge ${value.toLowerCase()}`}>{value}</span>;
}

function formatTimestamp(value: string | null): string {
  if (!value) return "尚未结束";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function NewTask({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: () => Promise<void>;
}) {
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [wizardError, setWizardError] = useState("");
  const [repositoryInspection, setRepositoryInspection] =
    useState<RepositoryInspection | null>(null);
  const [repositoryError, setRepositoryError] = useState("");
  const [repositoryMode, setRepositoryMode] =
    useState<"existing" | "new">("existing");
  const [projectParent, setProjectParent] = useState("");
  const [projectName, setProjectName] = useState("");
  const idempotencyKey = useRef(crypto.randomUUID());
  const [input, setInput] = useState<CreateTaskInput>({
    title: "",
    objective: "",
    repository: "",
    permission: "workspace-write",
    checks: [],
    maxRepairs: 2,
    manualConfirmation: false,
  });

  const inspectRepository = async (
    path: string,
  ): Promise<RepositoryInspection | null> => {
    setRepositoryError("");
    setWizardError("");
    const candidate = path.trim();
    if (!candidate) {
      setRepositoryInspection(null);
      setRepositoryError("请选择仓库，或输入仓库路径后点击“检查”。");
      return null;
    }
    try {
      const inspection = await desktopRequest<RepositoryInspection>(
        "repository/inspect",
        { path: candidate },
      );
      setRepositoryInspection(inspection);
      setInput((current) => ({
        ...current,
        repository: inspection.repository,
      }));
      return inspection;
    } catch (reason) {
      setRepositoryInspection(null);
      setRepositoryError(errorMessage(reason, "无法检查这个仓库。"));
      return null;
    }
  };

  const browseRepository = async () => {
    const selected = await chooseRepositoryFolder();
    if (selected) await inspectRepository(selected);
  };
  const browseProjectParent = async () => {
    const selected = await chooseProjectParentFolder();
    if (selected) {
      setProjectParent(selected);
      setRepositoryError("");
      setWizardError("");
    }
  };
  const steps = ["仓库", "目标", "权限", "验收", "确认"];
  const existingRepositoryVerified =
    repositoryInspection !== null &&
    repositoryInspection.repository === input.repository;
  const newProjectValid =
    Boolean(projectParent.trim()) &&
    Boolean(projectName.trim()) &&
    !/[<>:"/\\|?*\u0000-\u001f]/.test(projectName) &&
    ![".", ".."].includes(projectName.trim());
  const repositoryVerified =
    repositoryMode === "existing" ? existingRepositoryVerified : newProjectValid;
  const titleAndObjectiveValid =
    Boolean(input.title.trim()) && Boolean(input.objective.trim());
  const checksValid = input.checks.every((check) => Boolean(check.trim()));
  const currentStepValid =
    step === 1
      ? repositoryVerified
      : step === 2
        ? titleAndObjectiveValid
        : step === 4
          ? checksValid
          : true;
  const formValid = repositoryVerified && titleAndObjectiveValid && checksValid;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setWizardError("");
    if (step < 5) {
      if (!currentStepValid) {
        setWizardError(
          step === 1
            ? repositoryMode === "existing"
              ? "请先选择并成功检查一个 Git 仓库。"
              : "请选择保存位置并填写有效的项目名称。"
            : step === 2
              ? "请填写任务名称、目标和完成条件。"
              : "请修正验收设置后继续。",
        );
        return;
      }
      setStep(step + 1);
      return;
    }
    if (!formValid) {
      setWizardError("任务信息已经失效，请返回并重新检查。");
      return;
    }
    setSubmitting(true);
    try {
      let repository = input.repository;
      if (repositoryMode === "existing") {
        const latestInspection = await desktopRequest<RepositoryInspection>(
          "repository/inspect",
          { path: input.repository },
        );
        setRepositoryInspection(latestInspection);
        repository = latestInspection.repository;
      }
      await desktopRequest("task/create", {
        input: {
          ...input,
          repository,
          repositoryMode,
          projectParent:
            repositoryMode === "new" ? projectParent.trim() : undefined,
          projectName: repositoryMode === "new" ? projectName.trim() : undefined,
        },
        expectedVersion: 0,
        idempotencyKey: idempotencyKey.current,
      });
      await onCreated();
    } catch (reason) {
      setWizardError(errorMessage(reason, "无法创建任务，请检查仓库后重试。"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="wizard" onSubmit={(event) => void submit(event)}>
      <header className="wizard-header">
        <button type="button" className="icon-button" onClick={onCancel}>
          <ArrowLeft size={20} />
          <span className="sr-only">返回首页</span>
        </button>
        <div>
          <p className="eyebrow">隔离工作区</p>
          <h1>新建任务</h1>
        </div>
      </header>
      <ol className="stepper" aria-label="创建任务进度">
        {steps.map((label, index) => (
          <li
            key={label}
            className={step === index + 1 ? "active" : step > index + 1 ? "done" : ""}
          >
            <span>{step > index + 1 ? <Check size={15} /> : index + 1}</span>
            <b>{label}</b>
          </li>
        ))}
      </ol>
      <section className="wizard-panel">
        {step === 1 && (
          <>
            <p className="eyebrow">第 1 步</p>
            <h2>选择项目来源</h2>
            <div className="choice-grid">
              <Choice
                active={repositoryMode === "existing"}
                icon={FolderGit2}
                title="打开现有项目"
                text="选择已有的本地 Git 仓库，并创建隔离工作区。"
                onClick={() => {
                  setRepositoryMode("existing");
                  setRepositoryError("");
                  setWizardError("");
                }}
              />
              <Choice
                active={repositoryMode === "new"}
                icon={Plus}
                title="创建新项目"
                text="选择保存位置，应用会建立项目目录和本地 Git 仓库。"
                onClick={() => {
                  setRepositoryMode("new");
                  setRepositoryError("");
                  setWizardError("");
                }}
              />
            </div>
            {repositoryMode === "existing" ? (
              <label className="field">
                <span>仓库路径</span>
                <div className="input-with-action">
                  <FolderGit2 size={18} />
                  <input
                    value={input.repository}
                    placeholder="选择或输入本机 Git 仓库路径"
                    autoComplete="off"
                    aria-invalid={Boolean(repositoryError)}
                    onChange={(event) => {
                      setInput({ ...input, repository: event.target.value });
                      setRepositoryInspection(null);
                      setRepositoryError("");
                      setWizardError("");
                    }}
                  />
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => void inspectRepository(input.repository)}
                  >
                    检查
                  </button>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => void browseRepository()}
                  >
                    浏览
                  </button>
                </div>
              </label>
            ) : (
              <>
                <label className="field">
                  <span>保存位置</span>
                  <div className="input-with-action">
                    <FolderGit2 size={18} />
                    <input
                      value={projectParent}
                      placeholder="选择新项目所在的文件夹"
                      autoComplete="off"
                      onChange={(event) => {
                        setProjectParent(event.target.value);
                        setRepositoryError("");
                        setWizardError("");
                      }}
                    />
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => void browseProjectParent()}
                    >
                      浏览
                    </button>
                  </div>
                </label>
                <label className="field">
                  <span>项目名称</span>
                  <input
                    value={projectName}
                    placeholder="例如：我的桌面工具"
                    autoComplete="off"
                    aria-invalid={Boolean(projectName) && !newProjectValid}
                    onChange={(event) => {
                      setProjectName(event.target.value);
                      setRepositoryError("");
                      setWizardError("");
                    }}
                  />
                </label>
                <div className="notice safe">
                  <CheckCircle2 size={19} />
                  <div>
                    <strong>由应用完成基础设置</strong>
                    <span>确认创建后会建立项目目录、本地 Git 仓库和初始版本。</span>
                  </div>
                </div>
              </>
            )}
            {repositoryError && (
              <div className="notice warning" role="alert">
                <TriangleAlert size={19} />
                <div>
                  <strong>无法使用这个目录</strong>
                  <span>{repositoryError}</span>
                </div>
              </div>
            )}
            {repositoryMode === "existing" && repositoryInspection && (
              <div
                className={
                  repositoryInspection.dirty ? "notice warning" : "notice safe"
                }
              >
                {repositoryInspection.dirty ? (
                  <TriangleAlert size={19} />
                ) : (
                  <CheckCircle2 size={19} />
                )}
                <div>
                  <strong>
                    {repositoryInspection.branch} ·{" "}
                    {repositoryInspection.headRevision.slice(0, 8)}
                  </strong>
                  <span>
                    {repositoryInspection.dirty
                      ? `检测到 ${repositoryInspection.dirtyPathCount} 个未提交路径；不会纳入任务或改写原仓库。`
                      : "仓库干净，可以从当前提交创建隔离 Worktree。"}
                  </span>
                </div>
              </div>
            )}
          </>
        )}
        {step === 2 && (
          <>
            <p className="eyebrow">第 2 步</p>
            <h2>描述清楚的目标</h2>
            <label className="field">
              <span>任务名称</span>
              <input
                value={input.title}
                onChange={(event) => setInput({ ...input, title: event.target.value })}
              />
            </label>
            <label className="field">
              <span>目标和完成条件</span>
              <textarea
                rows={6}
                value={input.objective}
                onChange={(event) =>
                  setInput({ ...input, objective: event.target.value })
                }
              />
            </label>
          </>
        )}
        {step === 3 && (
          <>
            <p className="eyebrow">第 3 步</p>
            <h2>选择权限边界</h2>
            <div className="choice-grid">
              <Choice
                active={input.permission === "read-only"}
                icon={ShieldCheck}
                title="只读"
                text="Codex 可以检查代码和给出建议，不能修改文件。"
                onClick={() => setInput({ ...input, permission: "read-only" })}
              />
              <Choice
                active={input.permission === "workspace-write"}
                icon={Code2}
                title="工作区写入"
                text="只能修改隔离 Worktree；网络默认关闭。"
                onClick={() =>
                  setInput({ ...input, permission: "workspace-write" })
                }
              />
            </div>
            <div className="notice safe">
              <ShieldCheck size={19} />
              <div>
                <strong>高风险动作仍需逐次批准</strong>
                <span>提交、推送、合并和删除不会因本选项自动授权。</span>
              </div>
            </div>
          </>
        )}
        {step === 4 && (
          <>
            <p className="eyebrow">第 4 步</p>
            <h2>选择验收方式</h2>
            <div className="notice safe">
              <Sparkles size={19} />
              <div>
                <strong>AI 复核已开启</strong>
                <span>
                  Codex 会依据目标和完成条件复核自己的结果。报告会明确标注这是
                  AI 判断，不会冒充测试通过。
                </span>
              </div>
            </div>
            {repositoryInspection?.suggestedChecks.length ? (
              <div className="suggestion-list">
                <span className="field-label">从仓库中发现的可选命令</span>
                {repositoryInspection.suggestedChecks.map((suggestion) => {
                  const selected = input.checks.includes(suggestion.command);
                  return (
                    <button
                      type="button"
                      className="suggestion"
                      key={`${suggestion.source}:${suggestion.command}`}
                      disabled={selected}
                      onClick={() =>
                        setInput({
                          ...input,
                          checks: [...input.checks, suggestion.command],
                        })
                      }
                    >
                      <span>
                        <strong>{suggestion.label}</strong>
                        <small>依据：{suggestion.source}</small>
                      </span>
                      <code>{suggestion.command}</code>
                      <b>{selected ? "已添加" : "添加"}</b>
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="muted-copy">
                没有从仓库中发现可靠的测试命令。你可以直接继续，也可以自行添加。
              </p>
            )}
            <div className="check-editor">
              {input.checks.map((check, index) => (
                <label className="field" key={`${check}-${index}`}>
                  <span>可选自动检查 {index + 1}</span>
                  <div className="command-row">
                    <input
                      value={check}
                      onChange={(event) => {
                        const checks = [...input.checks];
                        checks[index] = event.target.value;
                        setInput({ ...input, checks });
                      }}
                    />
                    <button
                      type="button"
                      className="button quiet"
                      onClick={() =>
                        setInput({
                          ...input,
                          checks: input.checks.filter(
                            (_item, itemIndex) => itemIndex !== index,
                          ),
                        })
                      }
                    >
                      移除
                    </button>
                  </div>
                </label>
              ))}
              <button
                type="button"
                className="button secondary"
                onClick={() => setInput({ ...input, checks: [...input.checks, ""] })}
              >
                <Plus size={16} /> 添加项目命令
              </button>
            </div>
            {input.checks.length > 0 && (
              <label className="field compact-field">
                <span>命令失败后的最大自动修复次数</span>
                <input
                  type="number"
                  min={0}
                  max={20}
                  value={input.maxRepairs}
                  onChange={(event) =>
                    setInput({ ...input, maxRepairs: Number(event.target.value) })
                  }
                />
              </label>
            )}
            <label className="manual-confirmation">
              <input
                type="checkbox"
                checked={input.manualConfirmation}
                onChange={(event) =>
                  setInput({
                    ...input,
                    manualConfirmation: event.target.checked,
                  })
                }
              />
              <span>
                <strong>完成前由我最终确认</strong>
                <small>AI 和可选自动检查完成后，任务会等待你的确认。</small>
              </span>
            </label>
          </>
        )}
        {step === 5 && (
          <>
            <p className="eyebrow">最后确认</p>
            <h2>任务将在隔离环境中创建</h2>
            <div className="review-grid">
              <Review
                label={repositoryMode === "new" ? "新项目" : "仓库"}
                value={
                  repositoryMode === "new"
                    ? `${projectParent}\\${projectName}`
                    : input.repository
                }
              />
              <Review
                label="基准"
                value={
                  repositoryMode === "new"
                    ? "应用将创建初始版本"
                    : repositoryInspection
                    ? `${repositoryInspection.branch} @ ${repositoryInspection.headRevision.slice(0, 8)}`
                    : "创建时重新验证"
                }
              />
              <Review label="任务分支" value="aiao/task-new" />
              <Review label="权限" value={input.permission} />
              <Review label="AI 复核" value="默认开启（自我复核）" />
              <Review
                label="自动检查"
                value={input.checks.length ? `${input.checks.length} 项` : "未配置"}
              />
              <Review
                label="人工确认"
                value={input.manualConfirmation ? "完成前需要" : "不需要"}
              />
              <Review
                label="修复预算"
                value={input.checks.length ? `${input.maxRepairs} 次` : "不适用"}
              />
            </div>
            <div className="notice safe">
              <CheckCircle2 size={19} />
              <div>
                <strong>
                  {repositoryMode === "new" ? "新项目将在本机创建" : "原仓库保持不变"}
                </strong>
                <span>创建后任务进入“可以开始”，不会未经确认自动运行。</span>
              </div>
            </div>
          </>
        )}
      </section>
      {wizardError && (
        <div className="notice warning" role="alert">
          <TriangleAlert size={19} />
          <div>
            <strong>无法继续</strong>
            <span>{wizardError}</span>
          </div>
        </div>
      )}
      <footer className="wizard-footer">
        <button
          type="button"
          className="button secondary"
          onClick={() => (step === 1 ? onCancel() : setStep(step - 1))}
        >
          {step === 1 ? "取消" : "上一步"}
        </button>
        <button
          className="button primary"
          disabled={submitting || !currentStepValid}
        >
          {step === 5 ? (submitting ? "正在创建…" : "确认并创建") : "继续"}
          {!submitting && <ArrowRight size={17} />}
        </button>
      </footer>
    </form>
  );
}

function Approvals({
  approvals,
  onDecided,
}: {
  approvals: ApprovalSummary[];
  onDecided: () => Promise<void>;
}) {
  const decide = async (approval: ApprovalSummary, approved: boolean) => {
    await desktopRequest("approval/decide", {
      approvalId: approval.id,
      approved,
      expectedActionHash: approval.hash,
      idempotencyKey: crypto.randomUUID(),
    });
    await onDecided();
  };
  return (
    <div className="page-stack narrow-page">
      <section className="hero-row">
        <div>
          <p className="eyebrow">需要你判断</p>
          <h1>审批中心</h1>
          <p className="lede">每次批准只绑定当前参数、动作哈希和有效期。</p>
        </div>
      </section>
      {approvals.length ? (
        approvals.map((item) => (
          <section className="approval-card" key={item.id}>
            <div className="approval-icon">
              <ShieldCheck />
            </div>
            <div className="approval-content">
              <p className="eyebrow">高风险动作</p>
              <h2>{item.action}</h2>
              <p>{item.risk}</p>
              <dl>
                <div>
                  <dt>有效期</dt>
                  <dd>{item.expiresIn}</dd>
                </div>
                <div>
                  <dt>参数摘要哈希</dt>
                  <dd>{item.hash}</dd>
                </div>
                <div>
                  <dt>回滚方式</dt>
                  <dd>未推送前保留任务分支并人工撤销提交</dd>
                </div>
              </dl>
              <div className="button-row">
                <button
                  className="button danger-quiet"
                  onClick={() => void decide(item, false)}
                >
                  拒绝
                </button>
                <button
                  className="button primary"
                  onClick={() => void decide(item, true)}
                >
                  <ShieldCheck size={17} /> 批准一次
                </button>
              </div>
            </div>
          </section>
        ))
      ) : (
        <section className="empty-card">
          <CheckCircle2 size={32} />
          <h2>没有待处理审批</h2>
          <p>任务遇到需要判断的高风险动作时，会安全暂停并显示在这里。</p>
        </section>
      )}
    </div>
  );
}

function SettingsPage({
  snapshot,
  notificationsEnabled,
  onNotificationsChanged,
  onRefresh,
}: {
  snapshot: SystemSnapshot;
  notificationsEnabled: boolean;
  onNotificationsChanged: (enabled: boolean) => void;
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState("");
  const [status, setStatus] = useState("");
  const [maintenanceError, setMaintenanceError] = useState("");

  const runMaintenance = async (
    operation: string,
    action: () => Promise<string>,
  ) => {
    setBusy(operation);
    setStatus("");
    setMaintenanceError("");
    try {
      const completedStatus = await action();
      await onRefresh();
      setStatus(completedStatus);
    } catch (reason) {
      setMaintenanceError(
        reason instanceof Error ? reason.message : "维护操作没有完成。",
      );
    } finally {
      setBusy("");
    }
  };

  const createBackup = () =>
    runMaintenance("backup", async () => {
      const result = await desktopRequest<MaintenanceSummary>(
        "maintenance/backup",
      );
      return `备份 ${result.createdBackupId} 已完成并通过完整性检查。`;
    });

  const restoreLatest = () => {
    const latest = snapshot.maintenance.latestBackup;
    if (!latest) return;
    if (
      !window.confirm(
        "确认恢复最近备份？当前数据库会先创建安全副本，活动任务必须已经结束。恢复后建议重启应用。",
      )
    ) {
      return;
    }
    return runMaintenance("restore", async () => {
      const result = await desktopRequest<MaintenanceSummary>(
        "maintenance/restore",
        {
          backupId: latest.id,
          confirmation: "RESTORE_BACKUP",
        },
      );
      return result.safetyBackupCreated
        ? "恢复完成，恢复前安全副本已保留。请重启应用。"
        : "恢复完成。请重启应用。";
    });
  };

  const exportDiagnostics = () =>
    runMaintenance("diagnostics", async () => {
      const result = await desktopRequest<DiagnosticExport>(
        "maintenance/diagnostics",
      );
      return `诊断包已保存：${result.path}`;
    });

  const toggleNotifications = async () => {
    if (notificationsEnabled) {
      localStorage.setItem("aiao.notifications", "disabled");
      onNotificationsChanged(false);
      setStatus("Windows 本地通知已关闭。");
      return;
    }
    setBusy("notifications");
    setMaintenanceError("");
    const granted = await sendLocalNotification(
      "本地通知已开启",
      "任务完成或需要处理时，我们会在 Windows 中提醒你。",
    );
    setBusy("");
    if (!granted) {
      setMaintenanceError("Windows 没有授予通知权限，请在系统设置中开启。");
      return;
    }
    localStorage.setItem("aiao.notifications", "enabled");
    onNotificationsChanged(true);
    setStatus("Windows 本地通知已开启。");
  };

  return (
    <div className="page-stack narrow-page">
      <section className="hero-row">
        <div>
          <p className="eyebrow">本地维护</p>
          <h1>设置</h1>
          <p className="lede">程序、数据和 Codex 凭据彼此分离。</p>
        </div>
      </section>
      <section className="settings-list">
        <SettingsRow
          icon={KeyRound}
          title="Codex 账户"
          value={`${
            snapshot.account.email ||
            snapshot.account.accountType ||
            "Codex 账户"
          } · ${snapshot.account.signedIn ? "已连接" : "未登录"}`}
          action="凭据由 Codex 管理"
        />
        <SettingsRow
          icon={Clock3}
          title="启动与通知"
          value={`Windows 本地通知：${notificationsEnabled ? "开启" : "关闭"} · 登录后启动将在安装阶段提供`}
          action={notificationsEnabled ? "关闭通知" : "开启通知"}
          disabled={busy === "notifications"}
          onClick={() => void toggleNotifications()}
        />
        <SettingsRow
          icon={ShieldCheck}
          title="数据与备份"
          value={`最近备份：${snapshot.backupLabel} · 最多保留 ${snapshot.maintenance.backupRetention} 份`}
          action={busy === "backup" ? "备份中…" : "立即备份"}
          disabled={Boolean(busy)}
          onClick={() => void createBackup()}
        />
        <SettingsRow
          icon={RefreshCw}
          title="恢复最近备份"
          value={
            snapshot.maintenance.latestBackup
              ? `${snapshot.maintenance.latestBackup.id} · 恢复前自动创建安全副本`
              : "还没有可恢复的备份"
          }
          action={busy === "restore" ? "恢复中…" : "恢复"}
          disabled={
            Boolean(busy) || !snapshot.maintenance.restoreAvailable
          }
          danger
          onClick={() => void restoreLatest()}
        />
        <SettingsRow
          icon={Activity}
          title="日志与诊断"
          value="默认脱敏，不包含代码正文或凭据"
          action={busy === "diagnostics" ? "导出中…" : "导出诊断"}
          disabled={Boolean(busy)}
          onClick={() => void exportDiagnostics()}
        />
      </section>
      {status && (
        <div className="notice safe" role="status">
          <CheckCircle2 size={19} />
          <div>
            <strong>维护操作已完成</strong>
            <span>{status}</span>
          </div>
        </div>
      )}
      {maintenanceError && (
        <div className="notice danger" role="alert">
          <TriangleAlert size={19} />
          <div>
            <strong>维护操作未完成</strong>
            <span>{maintenanceError} 当前数据没有改变。</span>
          </div>
        </div>
      )}
      <section className="card about-card">
        <div>
          <p className="eyebrow">关于</p>
          <h2>AI Agent Orchestrator {snapshot.appVersion}</h2>
          <p>协议 {snapshot.protocol} · Schema v{snapshot.schemaVersion}</p>
        </div>
      </section>
    </div>
  );
}

function CodexLoginPanel({
  account,
  onRefresh,
}: {
  account: AccountSummary;
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [attempt, setAttempt] = useState<LoginAttempt | null>(null);
  const [loginError, setLoginError] = useState("");

  const waitForLogin = async (loginId: string) => {
    for (let index = 0; index < 150; index += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      const status = await desktopRequest<LoginAttempt>(
        "account/login/status",
        { loginId },
      );
      setAttempt(status);
      if (status.status === "SUCCEEDED") {
        await onRefresh();
        return;
      }
      if (status.status === "FAILED" || status.status === "CANCELLED") {
        throw new Error(status.error || "Codex 登录没有完成。");
      }
    }
    throw new Error("等待登录超时，请重试或使用设备代码。");
  };

  const startLogin = async (
    type: "chatgpt" | "chatgptDeviceCode",
  ) => {
    setBusy(true);
    setLoginError("");
    try {
      const started = await desktopRequest<LoginAttempt>(
        "account/login/start",
        { type },
      );
      setAttempt(started);
      const url = started.authorizationUrl || started.verificationUrl;
      if (url) await openTrustedLoginUrl(url);
      if (started.loginId) await waitForLogin(started.loginId);
    } catch (reason) {
      setLoginError(
        reason instanceof Error ? reason.message : "无法启动 Codex 登录。",
      );
    } finally {
      setBusy(false);
    }
  };

  const submitApiKey = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setLoginError("");
    try {
      await desktopRequest<LoginAttempt>("account/login/start", {
        type: "apiKey",
        apiKey,
      });
      setApiKey("");
      await onRefresh();
    } catch (reason) {
      setLoginError(
        reason instanceof Error ? reason.message : "API Key 登录失败。",
      );
    } finally {
      setBusy(false);
    }
  };

  if (account.signedIn) {
    return (
      <div className="notice safe">
        <CheckCircle2 size={19} />
        <div>
          <strong>Codex 已登录</strong>
          <span>{account.email || account.accountType || "账户可用"}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="login-panel">
      <div className="button-row">
        <button
          className="button primary"
          disabled={busy}
          onClick={() => void startLogin("chatgpt")}
        >
          <KeyRound size={17} /> 使用 ChatGPT 登录
        </button>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => void startLogin("chatgptDeviceCode")}
        >
          使用设备代码
        </button>
      </div>
      {attempt?.userCode && (
        <div className="notice safe">
          <KeyRound size={19} />
          <div>
            <strong>设备代码：{attempt.userCode}</strong>
            <span>已打开验证页面。代码只用于本次登录。</span>
          </div>
        </div>
      )}
      <form className="api-key-login" onSubmit={(event) => void submitApiKey(event)}>
        <label className="field">
          <span>或者使用 API Key</span>
          <input
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="仅传给 Codex，不会写入任务数据库"
          />
        </label>
        <button className="button secondary" disabled={busy || !apiKey.trim()}>
          使用 API Key
        </button>
      </form>
      {loginError && <p className="field-error" role="alert">{loginError}</p>}
    </div>
  );
}

function Onboarding({
  snapshot,
  onRefresh,
  onComplete,
}: {
  snapshot: SystemSnapshot;
  onRefresh: () => Promise<void>;
  onComplete: () => void;
}) {
  const [step, setStep] = useState(1);
  const content = [
    {
      eyebrow: "任务编排",
      title: "为长期开发任务建立\n可恢复的执行流程。",
      text: "在独立 Git Worktree 中运行 Codex，并保留检查点、审批记录与验收证据。",
      icon: Sparkles,
    },
    {
      eyebrow: "连接检查",
      title: "确认本地服务与\nCodex 账户状态。",
      text: "开始任务前，请确认本地服务正常，并完成 Codex 账户连接。",
      icon: Gauge,
    },
    {
      eyebrow: "数据边界",
      title: "任务状态由本机保存，\n权限由你明确授予。",
      text: "本应用不提供云端控制服务；Codex 访问遵循你的账户设置，凭据不会写入任务数据库或备份。",
      icon: ShieldCheck,
    },
  ][step - 1];
  const Icon = content.icon;
  return (
    <main className="onboarding">
      <div className="onboarding-art" aria-hidden="true">
        <div className="orbit one" />
        <div className="orbit two" />
        <div className="core">
          <Icon />
        </div>
        <span className="node node-a"><FolderGit2 /></span>
        <span className="node node-b"><Bot /></span>
        <span className="node node-c"><FileCheck2 /></span>
      </div>
      <section className="onboarding-card">
        <div className="brand compact">
          <div className="brand-mark"><Sparkles /></div>
          <strong>AI Agent Orchestrator</strong>
        </div>
        <div className="onboarding-copy">
          <p className="eyebrow">{content.eyebrow}</p>
          <h1>{content.title}</h1>
          <p>{content.text}</p>
          {step === 2 && (
            <>
              <ul className="ready-list">
                <HealthRow
                  label="本地服务"
                  value={snapshot.healthy ? "正常" : "需要检查"}
                />
                <HealthRow
                  label="任务数据"
                  value={`本机 Schema v${snapshot.schemaVersion}`}
                />
                <HealthRow
                  label="Codex"
                  value={snapshot.account.signedIn ? "已登录" : "需要登录"}
                />
              </ul>
              <CodexLoginPanel
                account={snapshot.account}
                onRefresh={onRefresh}
              />
            </>
          )}
        </div>
        <div className="onboarding-footer">
          <div className="dots" aria-label={`第 ${step} 步，共 3 步`}>
            {[1, 2, 3].map((item) => (
              <span className={item === step ? "active" : ""} key={item} />
            ))}
          </div>
          <div className="button-row">
            {step > 1 && (
              <button className="button secondary" onClick={() => setStep(step - 1)}>
                上一步
              </button>
            )}
            <button
              className="button primary"
              disabled={step === 2 && !snapshot.account.signedIn}
              onClick={() => (step === 3 ? onComplete() : setStep(step + 1))}
            >
              {step === 3 ? "进入工作台" : "继续"}
              <ArrowRight size={17} />
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}

function StateBadge({
  state,
  compact = false,
}: {
  state: TaskState;
  compact?: boolean;
}) {
  const Icon =
    state === "RUNNING"
      ? CirclePlay
      : state === "PAUSED"
        ? CirclePause
        : state === "SUCCEEDED"
          ? CheckCircle2
          : state === "NEEDS_ATTENTION"
            ? TriangleAlert
            : Clock3;
  return (
    <span className={`state-badge state-${state.toLowerCase()} ${compact ? "compact" : ""}`}>
      <Icon size={compact ? 14 : 16} />
      {stateLabels[state]}
    </span>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  note,
}: {
  icon: typeof Home;
  label: string;
  value: string;
  note: string;
}) {
  return (
    <div className="metric">
      <span className="metric-icon"><Icon size={19} /></span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{note}</small>
      </div>
    </div>
  );
}

function ActivityList({
  activities,
}: {
  activities: SystemSnapshot["activities"];
}) {
  return (
    <ol className="activity-list">
      {activities.map((item) => (
        <li key={item.id}>
          <span className={`activity-dot ${item.tone}`} />
          <div>
            <strong>{item.title}</strong>
            <span>{item.detail}</span>
          </div>
          <time>{item.time}</time>
        </li>
      ))}
    </ol>
  );
}

function HealthRow({ label, value }: { label: string; value: string }) {
  return (
    <li className="health-row">
      <CheckCircle2 size={17} />
      <span>{label}</span>
      <strong>{value}</strong>
    </li>
  );
}

function Fact({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Home;
  label: string;
  value: string;
}) {
  return (
    <div className="fact">
      <Icon size={19} />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function Choice({
  active,
  icon: Icon,
  title,
  text,
  onClick,
}: {
  active: boolean;
  icon: typeof Home;
  title: string;
  text: string;
  onClick: () => void;
}) {
  return (
    <button
      className={active ? "choice active" : "choice"}
      type="button"
      onClick={onClick}
      aria-pressed={active}
    >
      <span><Icon size={21} /></span>
      <strong>{title}</strong>
      <p>{text}</p>
      {active && <CheckCircle2 className="choice-check" size={20} />}
    </button>
  );
}

function Review({ label, value }: { label: string; value: string }) {
  return (
    <div className="review-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function SettingsRow({
  icon: Icon,
  title,
  value,
  action,
  disabled = false,
  danger = false,
  onClick,
}: {
  icon: typeof Home;
  title: string;
  value: string;
  action: string;
  disabled?: boolean;
  danger?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      className={`settings-row ${danger ? "danger" : ""}`}
      disabled={disabled || !onClick}
      onClick={onClick}
    >
      <span className="settings-icon"><Icon size={20} /></span>
      <span className="settings-copy">
        <strong>{title}</strong>
        <small>{value}</small>
      </span>
      <span className="settings-action">{action}</span>
      <ChevronRight size={18} />
    </button>
  );
}

export default App;
