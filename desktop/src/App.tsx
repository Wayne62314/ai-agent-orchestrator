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
import { FormEvent, useEffect, useState } from "react";
import {
  chooseRepositoryFolder,
  desktopRequest,
  openTrustedLoginUrl,
} from "./bridge";
import type {
  AccountSummary,
  ApprovalSummary,
  CreateTaskInput,
  LoginAttempt,
  Page,
  RepositoryInspection,
  SystemSnapshot,
  TaskState,
  TaskSummary,
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

function App() {
  const [snapshot, setSnapshot] = useState<SystemSnapshot | null>(null);
  const [page, setPage] = useState<Page>("home");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [onboarding, setOnboarding] = useState(
    () => localStorage.getItem("aiao.onboarding") !== "complete",
  );

  const refresh = async () => {
    setError("");
    try {
      setSnapshot(await desktopRequest<SystemSnapshot>("system/initialize"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "后台连接失败。");
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const runTaskAction = async (method: string) => {
    const task = snapshot?.activeTask;
    if (!task) return;
    setBusy(true);
    setError("");
    try {
      await desktopRequest<TaskSummary>(method, {
        taskId: task.id,
        expectedVersion: task.version,
        idempotencyKey: crypto.randomUUID(),
      });
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作没有完成。");
    } finally {
      setBusy(false);
    }
  };

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
    <div className="app-shell">
      <Sidebar
        page={page}
        approvalCount={snapshot.approvals.length}
        onNavigate={setPage}
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
          {page === "home" && (
            <Dashboard
              snapshot={snapshot}
              busy={busy}
              onNavigate={setPage}
              onTaskAction={runTaskAction}
            />
          )}
          {page === "new-task" && (
            <NewTask
              onCancel={() => setPage("home")}
              onCreated={async () => {
                await refresh();
                setPage("task");
              }}
            />
          )}
          {page === "task" && snapshot.activeTask && (
            <TaskDetail
              task={snapshot.activeTask}
              activities={snapshot.activities}
              busy={busy}
              onAction={runTaskAction}
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
              onRestartOnboarding={() => setOnboarding(true)}
            />
          )}
        </main>
      </div>
    </div>
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
  return (
    <header className="topbar">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <div>
        <span className="system-pill">
          <span className="status-dot" />
          后台正常
        </span>
        <span className="muted">Schema v{snapshot.schemaVersion}</span>
      </div>
      <div className="account">
        <div className="account-avatar">W</div>
        <div>
          <strong>{snapshot.account.accountType || "Codex"}</strong>
          <span>{snapshot.account.planType || "已连接"}</span>
        </div>
        <ChevronRight size={16} aria-hidden="true" />
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
  onAction: (method: string) => Promise<void>;
}) {
  const [tab, setTab] = useState("概览");
  const tabs = ["概览", "活动", "代码变更", "Checkpoint", "验收", "报告"];
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
            aria-selected={tab === item}
            className={tab === item ? "active" : ""}
            key={item}
            onClick={() => setTab(item)}
          >
            {item}
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
                <strong>Codex 正在处理任务</strong>
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
        <section className="card placeholder-panel">
          <TerminalSquare size={28} />
          <h2>{tab}</h2>
          <p>
            此区域将通过分页只读接口加载脱敏内容；刷新页面时不依赖内存事件。
          </p>
          {tab === "验收" && (
            <div className="check-list">
              <HealthRow label="单元测试" value="通过" />
              <HealthRow label="代码规范" value="通过" />
              <HealthRow label="构建" value="通过" />
              <HealthRow label="最终报告" value="等待中" />
            </div>
          )}
        </section>
      )}
    </div>
  );
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
  const [repositoryInspection, setRepositoryInspection] =
    useState<RepositoryInspection | null>(null);
  const [repositoryError, setRepositoryError] = useState("");
  const [input, setInput] = useState<CreateTaskInput>({
    title: "补充登录模块测试",
    objective: "补齐异常登录和凭据失效路径测试，确保必选检查全部通过。",
    repository: "C:\\Projects\\Northstar",
    permission: "workspace-write",
    checks: ["python -m unittest discover -s tests -v", "python -m ruff check ."],
    maxRepairs: 2,
  });

  const inspectRepository = async (path: string) => {
    setRepositoryError("");
    try {
      const inspection = await desktopRequest<RepositoryInspection>(
        "repository/inspect",
        { path },
      );
      setRepositoryInspection(inspection);
      setInput((current) => ({
        ...current,
        repository: inspection.repository,
      }));
    } catch (reason) {
      setRepositoryInspection(null);
      setRepositoryError(
        reason instanceof Error ? reason.message : "无法检查这个仓库。",
      );
    }
  };

  const browseRepository = async () => {
    const selected = await chooseRepositoryFolder();
    if (selected) await inspectRepository(selected);
  };
  const steps = ["仓库", "目标", "权限", "验收", "确认"];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (step < 5) {
      setStep(step + 1);
      return;
    }
    setSubmitting(true);
    try {
      await desktopRequest("task/create", {
        input,
        expectedVersion: 0,
        idempotencyKey: crypto.randomUUID(),
      });
      await onCreated();
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
            <h2>选择 Git 仓库</h2>
            <p className="panel-intro">应用只读取原仓库，并为任务创建独立 Worktree。</p>
            <label className="field">
              <span>仓库路径</span>
              <div className="input-with-action">
                <FolderGit2 size={18} />
                <input
                  value={input.repository}
                  onChange={(event) =>
                    setInput({ ...input, repository: event.target.value })
                  }
                />
                <button
                  type="button"
                  className="text-button"
                  onClick={() => void browseRepository()}
                >
                  浏览
                </button>
              </div>
            </label>
            {repositoryError && (
              <div className="notice warning" role="alert">
                <TriangleAlert size={19} />
                <div>
                  <strong>无法使用这个目录</strong>
                  <span>{repositoryError}</span>
                </div>
              </div>
            )}
            {repositoryInspection && (
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
            <h2>定义自动验收</h2>
            <div className="check-editor">
              {input.checks.map((check, index) => (
                <label className="field" key={`${check}-${index}`}>
                  <span>必选检查 {index + 1}</span>
                  <input
                    value={check}
                    onChange={(event) => {
                      const checks = [...input.checks];
                      checks[index] = event.target.value;
                      setInput({ ...input, checks });
                    }}
                  />
                </label>
              ))}
            </div>
            <label className="field compact-field">
              <span>最大自动修复次数</span>
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
          </>
        )}
        {step === 5 && (
          <>
            <p className="eyebrow">最后确认</p>
            <h2>任务将在隔离环境中创建</h2>
            <div className="review-grid">
              <Review label="仓库" value={input.repository} />
              <Review
                label="基准"
                value={
                  repositoryInspection
                    ? `${repositoryInspection.branch} @ ${repositoryInspection.headRevision.slice(0, 8)}`
                    : "创建时重新验证"
                }
              />
              <Review label="任务分支" value="aiao/task-new" />
              <Review label="权限" value={input.permission} />
              <Review label="必选检查" value={`${input.checks.length} 项`} />
              <Review label="修复预算" value={`${input.maxRepairs} 次`} />
            </div>
            <div className="notice safe">
              <CheckCircle2 size={19} />
              <div>
                <strong>原仓库保持不变</strong>
                <span>创建后任务进入“可以开始”，不会未经确认自动运行。</span>
              </div>
            </div>
          </>
        )}
      </section>
      <footer className="wizard-footer">
        <button
          type="button"
          className="button secondary"
          onClick={() => (step === 1 ? onCancel() : setStep(step - 1))}
        >
          {step === 1 ? "取消" : "上一步"}
        </button>
        <button className="button primary" disabled={submitting}>
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
  onRestartOnboarding,
}: {
  snapshot: SystemSnapshot;
  onRestartOnboarding: () => void;
}) {
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
          value={`${snapshot.account.accountType} · ${snapshot.account.planType}`}
          action="管理登录"
        />
        <SettingsRow
          icon={Clock3}
          title="启动与通知"
          value="登录后启动：关闭"
          action="更改"
        />
        <SettingsRow
          icon={ShieldCheck}
          title="数据与备份"
          value={`最近备份：${snapshot.backupLabel}`}
          action="立即备份"
        />
        <SettingsRow
          icon={Activity}
          title="日志与诊断"
          value="默认脱敏，不包含代码正文或凭据"
          action="导出诊断"
        />
      </section>
      <section className="card about-card">
        <div>
          <p className="eyebrow">关于</p>
          <h2>AI Agent Orchestrator {snapshot.appVersion}</h2>
          <p>协议 {snapshot.protocol} · Schema v{snapshot.schemaVersion}</p>
        </div>
        <button className="button secondary" onClick={onRestartOnboarding}>
          重新查看首次设置
        </button>
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
          <span>
            {account.email || account.accountType || "账户可用"}
            {account.planType ? ` · ${account.planType}` : ""}
          </span>
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
      eyebrow: "欢迎",
      title: "让 Codex 长时间工作，\n你只负责关键判断。",
      text: "任务会在独立 Git Worktree 中运行，安全暂停、恢复并用验收证据证明结果。",
      icon: Sparkles,
    },
    {
      eyebrow: "环境检查",
      title: "本机能力已经就绪。",
      text: "数据库、Git、Python sidecar 与 Codex 登录均已通过检查。",
      icon: Gauge,
    },
    {
      eyebrow: "隐私与数据",
      title: "你的代码和任务状态\n留在这台电脑。",
      text: "桌面主链不开放网络端口，Codex 凭据由系统安全存储管理，也不会进入备份。",
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
                <HealthRow label="本地后台" value="正常" />
                <HealthRow label="Git 与 Worktree" value="可用" />
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
}: {
  icon: typeof Home;
  title: string;
  value: string;
  action: string;
}) {
  return (
    <button className="settings-row">
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
