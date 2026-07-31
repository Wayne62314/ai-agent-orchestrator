using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace CodexWindowDockingSpike;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);

        if (args.Contains("--probe", StringComparer.OrdinalIgnoreCase))
        {
            return RunProbe(args);
        }

        ApplicationConfiguration.Initialize();
        WindowRecoveryJournal.TryRestore();
        var smokeIndex = Array.FindIndex(args, value => value.Equals("--attach-smoke", StringComparison.OrdinalIgnoreCase));
        var smokeOutput = smokeIndex >= 0 && smokeIndex + 1 < args.Length
            ? Path.GetFullPath(args[smokeIndex + 1])
            : null;
        Application.Run(new DockingForm(smokeOutput));
        return 0;
    }

    private static int RunProbe(string[] args)
    {
        var candidates = CodexWindowFinder.FindCandidates();
        var payload = JsonSerializer.Serialize(
            new
            {
                recordedAtUtc = DateTimeOffset.UtcNow,
                supported = OperatingSystem.IsWindows(),
                candidateCount = candidates.Count,
                candidates = candidates.Select(candidate => new
                {
                    handle = candidate.Handle.ToInt64(),
                    candidate.ProcessId,
                    candidate.Title,
                    candidate.ClassName,
                    candidate.ExecutablePath,
                    candidate.Area,
                }),
            },
            new JsonSerializerOptions { WriteIndented = true });

        var outputIndex = Array.FindIndex(args, value => value.Equals("--output", StringComparison.OrdinalIgnoreCase));
        if (outputIndex >= 0 && outputIndex + 1 < args.Length)
        {
            File.WriteAllText(Path.GetFullPath(args[outputIndex + 1]), payload, Encoding.UTF8);
        }

        Console.WriteLine(payload);
        return candidates.Count > 0 ? 0 : 2;
    }
}

internal sealed class DockingForm : Form
{
    private readonly Panel _hostPanel = new()
    {
        Dock = DockStyle.Fill,
        BackColor = Color.FromArgb(244, 244, 241),
        Margin = Padding.Empty,
    };

    private readonly Label _status = new()
    {
        AutoSize = true,
        Text = "尚未附着 Codex",
        ForeColor = Color.FromArgb(65, 76, 70),
        Margin = new Padding(10, 10, 10, 0),
    };

    private readonly Button _attachButton = new()
    {
        AutoSize = true,
        Text = "附着真实 Codex 窗口",
        Margin = new Padding(8),
    };

    private readonly Button _detachButton = new()
    {
        AutoSize = true,
        Text = "安全脱离",
        Enabled = false,
        Margin = new Padding(8),
    };

    private readonly Button _openButton = new()
    {
        AutoSize = true,
        Text = "在 Codex 中打开新任务",
        Margin = new Padding(8),
    };

    private readonly Label _emptyState = new()
    {
        AutoSize = false,
        Dock = DockStyle.Fill,
        TextAlign = ContentAlignment.MiddleCenter,
        Text = "把官方 Codex 窗口拖到这里\r\n\r\n接近时会显示磁吸预览，松开后附着",
        ForeColor = Color.FromArgb(86, 96, 91),
        Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Regular),
    };

    private WindowAttachment? _attachment;
    private readonly string? _smokeOutput;
    private readonly Panel _dockHandle = new()
    {
        Dock = DockStyle.Top,
        Height = 38,
        Visible = false,
        BackColor = Color.FromArgb(229, 242, 235),
        Cursor = Cursors.SizeAll,
        Padding = new Padding(12, 8, 12, 6),
    };
    private readonly Label _dockHandleLabel = new()
    {
        Dock = DockStyle.Fill,
        Text = "官方 Codex · 已附着　拖动此处可脱离",
        TextAlign = ContentAlignment.MiddleLeft,
        ForeColor = Color.FromArgb(31, 91, 66),
        Cursor = Cursors.SizeAll,
    };
    private readonly MagnetOverlay _magnetOverlay = new();
    private readonly System.Windows.Forms.Timer _monitorRefreshTimer = new() { Interval = 1500 };
    private MagneticWindowMonitor? _magnetMonitor;
    private int? _monitoredProcessId;
    private nint _movingWindow;
    private DateTimeOffset? _magnetEnteredAt;
    private bool _magnetArmed;

    public DockingForm(string? smokeOutput = null)
    {
        _smokeOutput = smokeOutput;
        Text = "Agent Dock — 官方 Codex 磁吸附着验证 V2";
        MinimumSize = new Size(920, 640);
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1280, 820);
        Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular);
        BackColor = Color.FromArgb(250, 250, 247);

        var toolbar = new FlowLayoutPanel
        {
            Dock = DockStyle.Top,
            AutoSize = true,
            WrapContents = true,
            Padding = new Padding(10, 6, 10, 6),
            BackColor = Color.White,
        };
        toolbar.Controls.Add(_attachButton);
        toolbar.Controls.Add(_detachButton);
        toolbar.Controls.Add(_openButton);
        toolbar.Controls.Add(_status);

        _dockHandle.Controls.Add(_dockHandleLabel);
        _hostPanel.Controls.Add(_emptyState);
        Controls.Add(_hostPanel);
        Controls.Add(_dockHandle);
        Controls.Add(toolbar);

        _attachButton.Click += (_, _) => AttachCodex();
        _detachButton.Click += (_, _) => DetachCodex();
        _openButton.Click += (_, _) => OpenCodexTask();
        _hostPanel.Resize += (_, _) => _attachment?.ResizeToHost();
        _dockHandle.MouseDown += DockHandleMouseDown;
        _dockHandleLabel.MouseDown += DockHandleMouseDown;
        _monitorRefreshTimer.Tick += (_, _) => RefreshMagnetMonitor();
        Shown += (_, _) =>
        {
            RefreshMagnetMonitor();
            _monitorRefreshTimer.Start();
        };
        FormClosing += (_, _) =>
        {
            _monitorRefreshTimer.Stop();
            _magnetMonitor?.Dispose();
            _magnetOverlay.Dispose();
            DetachCodex();
        };

        if (_smokeOutput is not null)
        {
            Shown += (_, _) => BeginInvoke(RunAttachSmoke);
        }
    }

    private void RunAttachSmoke()
    {
        var candidate = CodexWindowFinder.FindCandidates().FirstOrDefault();
        var originalParent = candidate is null ? nint.Zero : NativeMethods.GetParent(candidate.Handle);
        AttachCodex();
        var attached = _attachment is not null;
        var attachMessage = _status.Text;

        var timer = new System.Windows.Forms.Timer { Interval = 2000 };
        timer.Tick += (_, _) =>
        {
            timer.Stop();
            timer.Dispose();
            DetachCodex();

            var restored = candidate is not null
                && NativeMethods.IsWindow(candidate.Handle)
                && NativeMethods.GetParent(candidate.Handle) == originalParent;
            var payload = JsonSerializer.Serialize(
                new
                {
                    recordedAtUtc = DateTimeOffset.UtcNow,
                    candidateFound = candidate is not null,
                    magnetMonitoring = _magnetMonitor is not null,
                    attached,
                    restored,
                    attachMessage,
                    finalMessage = _status.Text,
                    processId = candidate?.ProcessId,
                    handle = candidate?.Handle.ToInt64(),
                },
                new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(_smokeOutput!, payload, Encoding.UTF8);
            Close();
        };
        timer.Start();
    }

    private void AttachCodex()
    {
        if (_attachment is not null)
        {
            return;
        }

        try
        {
            var candidate = CodexWindowFinder.FindCandidates().FirstOrDefault();
            if (candidate is null)
            {
                SetStatus("没有找到可见的官方 Codex 窗口。请先打开 Codex。", isError: true);
                return;
            }

            _attachment = WindowAttachment.Attach(candidate.Handle, _hostPanel, candidate.ProcessId);
            _emptyState.Visible = false;
            _dockHandle.Visible = true;
            _attachButton.Enabled = false;
            _detachButton.Enabled = true;
            SetStatus($"已附着官方 Codex（进程 {candidate.ProcessId}）", isError: false);
        }
        catch (Exception exception)
        {
            WindowRecoveryJournal.TryRestore();
            _attachment = null;
            _emptyState.Visible = true;
            _dockHandle.Visible = false;
            SetStatus($"附着失败：{exception.Message}", isError: true);
        }
    }

    private void AttachCodexWindow(nint window, int processId)
    {
        _attachment = WindowAttachment.Attach(window, _hostPanel, processId);
        _emptyState.Visible = false;
        _dockHandle.Visible = true;
        _attachButton.Enabled = false;
        _detachButton.Enabled = true;
        SetStatus($"已附着官方 Codex（进程 {processId}）", isError: false);
    }

    private void DetachCodex()
    {
        if (_attachment is null)
        {
            return;
        }

        try
        {
            _attachment.Dispose();
            SetStatus("Codex 已安全脱离并恢复为独立窗口", isError: false);
        }
        catch (Exception exception)
        {
            SetStatus($"脱离失败：{exception.Message}", isError: true);
        }
        finally
        {
            _attachment = null;
            _emptyState.Visible = true;
            _attachButton.Enabled = true;
            _detachButton.Enabled = false;
        }
    }

    private void DockHandleMouseDown(object? sender, MouseEventArgs eventArgs)
    {
        if (eventArgs.Button != MouseButtons.Left || _attachment is null)
        {
            return;
        }

        var window = _attachment.Window;
        DetachCodex();
        if (!NativeMethods.IsWindow(window))
        {
            return;
        }

        NativeMethods.GetVisibleWindowRect(window, out var rect);
        var cursor = Cursor.Position;
        var width = Math.Max(640, rect.Right - rect.Left);
        var height = Math.Max(480, rect.Bottom - rect.Top);
        NativeMethods.SetWindowPos(
            window,
            nint.Zero,
            cursor.X - Math.Min(160, width / 3),
            cursor.Y - 16,
            width,
            height,
            NativeMethods.SwpNoZOrder | NativeMethods.SwpShowWindow);
        NativeMethods.ReleaseCapture();
        NativeMethods.SendMessage(
            window,
            NativeMethods.WmNcLButtonDown,
            new nint(NativeMethods.HtCaption),
            nint.Zero);
    }

    private void RefreshMagnetMonitor()
    {
        if (_attachment is not null)
        {
            return;
        }

        var candidate = CodexWindowFinder.FindCandidates().FirstOrDefault();
        if (candidate is null)
        {
            _magnetMonitor?.Dispose();
            _magnetMonitor = null;
            _monitoredProcessId = null;
            return;
        }

        if (_monitoredProcessId == candidate.ProcessId && _magnetMonitor is not null)
        {
            return;
        }

        _magnetMonitor?.Dispose();
        try
        {
            _magnetMonitor = new MagneticWindowMonitor(candidate.ProcessId, HandleCodexWindowEvent);
            _monitoredProcessId = candidate.ProcessId;
            SetStatus("已发现官方 Codex；拖动窗口靠近中央区域即可磁吸", isError: false);
        }
        catch (Win32Exception exception)
        {
            _magnetMonitor = null;
            _monitoredProcessId = null;
            SetStatus($"无法启用磁吸监听：{exception.Message}", isError: true);
        }
    }

    private void HandleCodexWindowEvent(MagneticWindowEvent windowEvent)
    {
        if (IsDisposed || Disposing)
        {
            return;
        }

        if (InvokeRequired)
        {
            BeginInvoke(() => HandleCodexWindowEvent(windowEvent));
            return;
        }

        if (_attachment is not null || !CodexWindowFinder.IsEligibleWindow(windowEvent.Window))
        {
            return;
        }

        switch (windowEvent.Kind)
        {
            case MagneticWindowEventKind.MoveStarted:
                _movingWindow = windowEvent.Window;
                _magnetEnteredAt = null;
                _magnetArmed = false;
                break;
            case MagneticWindowEventKind.LocationChanged when windowEvent.Window == _movingWindow:
                UpdateMagnetPreview(windowEvent.Window);
                break;
            case MagneticWindowEventKind.MoveEnded when windowEvent.Window == _movingWindow:
                FinishMagnetMove(windowEvent.Window);
                break;
        }
    }

    private void UpdateMagnetPreview(nint window)
    {
        if (!NativeMethods.GetVisibleWindowRect(window, out var windowRect))
        {
            return;
        }

        var target = _hostPanel.RectangleToScreen(_hostPanel.ClientRectangle);
        var distance = MagnetGeometry.Distance(windowRect, target);
        var threshold = _magnetEnteredAt is null ? 48 : 80;
        if (distance > threshold)
        {
            ResetMagnetPreview();
            return;
        }

        _magnetEnteredAt ??= DateTimeOffset.UtcNow;
        _magnetArmed = DateTimeOffset.UtcNow - _magnetEnteredAt >= TimeSpan.FromMilliseconds(150);
        _magnetOverlay.ShowPreview(target, _magnetArmed);
        SetStatus(
            _magnetArmed ? "松开鼠标，将官方 Codex 附着到工作台" : "已进入磁吸范围…",
            isError: false);
    }

    private void FinishMagnetMove(nint window)
    {
        UpdateMagnetPreview(window);
        var shouldAttach = _magnetArmed;
        ResetMagnetPreview();
        _movingWindow = nint.Zero;

        if (!shouldAttach)
        {
            SetStatus("Codex 保持为独立窗口", isError: false);
            return;
        }

        NativeMethods.GetWindowThreadProcessId(window, out var processId);
        try
        {
            AttachCodexWindow(window, (int)processId);
        }
        catch (Exception exception)
        {
            WindowRecoveryJournal.TryRestore();
            SetStatus($"磁吸附着失败，Codex 保持独立：{exception.Message}", isError: true);
        }
    }

    private void ResetMagnetPreview()
    {
        _magnetOverlay.Hide();
        _magnetEnteredAt = null;
        _magnetArmed = false;
    }

    private void OpenCodexTask()
    {
        var workspace = Uri.EscapeDataString(Environment.CurrentDirectory);
        var prompt = Uri.EscapeDataString("这是 Agent Dock 创建的新任务。请先理解目标，等待用户进一步说明。");
        var deepLink = $"codex://threads/new?path={workspace}&prompt={prompt}";
        Process.Start(new ProcessStartInfo(deepLink) { UseShellExecute = true });
        SetStatus("已请求官方 Codex 打开新任务；提示词不会自动发送", isError: false);
    }

    private void SetStatus(string message, bool isError)
    {
        _status.Text = message;
        _status.ForeColor = isError ? Color.FromArgb(168, 52, 47) : Color.FromArgb(39, 104, 75);
    }
}

internal sealed class WindowAttachment : IDisposable
{
    private readonly nint _window;
    private readonly Control _host;
    private readonly nint _originalParent;
    private readonly nint _originalStyle;
    private readonly nint _originalExtendedStyle;
    private readonly NativeMethods.WindowPlacement _originalPlacement;
    private bool _disposed;

    public nint Window => _window;

    private WindowAttachment(
        nint window,
        Control host,
        nint originalParent,
        nint originalStyle,
        nint originalExtendedStyle,
        NativeMethods.WindowPlacement originalPlacement)
    {
        _window = window;
        _host = host;
        _originalParent = originalParent;
        _originalStyle = originalStyle;
        _originalExtendedStyle = originalExtendedStyle;
        _originalPlacement = originalPlacement;
    }

    public static WindowAttachment Attach(nint window, Control host, int processId)
    {
        if (!NativeMethods.IsWindow(window))
        {
            throw new InvalidOperationException("目标 Codex 窗口已经关闭。");
        }

        var placement = NativeMethods.WindowPlacement.Create();
        NativeMethods.ThrowIfFalse(
            NativeMethods.GetWindowPlacement(window, ref placement),
            "无法读取 Codex 窗口位置");

        var originalParent = NativeMethods.GetParent(window);
        var originalStyle = NativeMethods.GetWindowLongPtr(window, NativeMethods.GwlStyle);
        var originalExtendedStyle = NativeMethods.GetWindowLongPtr(window, NativeMethods.GwlExStyle);
        WindowRecoveryJournal.Save(
            window,
            processId,
            originalParent,
            originalStyle,
            originalExtendedStyle,
            placement);

        var embeddedStyle = new nint(
            (originalStyle.ToInt64()
                & ~NativeMethods.TopLevelWindowStyleMask)
            | NativeMethods.WsChild
            | NativeMethods.WsVisible);

        NativeMethods.SetWindowLongPtrChecked(window, NativeMethods.GwlStyle, embeddedStyle);
        NativeMethods.SetWindowLongPtrChecked(
            window,
            NativeMethods.GwlExStyle,
            new nint(originalExtendedStyle.ToInt64() & ~NativeMethods.WsExAppWindow));

        Marshal.SetLastPInvokeError(0);
        var previousParent = NativeMethods.SetParent(window, host.Handle);
        var parentError = Marshal.GetLastWin32Error();
        if (previousParent == nint.Zero && parentError != 0)
        {
            NativeMethods.SetWindowLongPtrChecked(window, NativeMethods.GwlStyle, originalStyle);
            NativeMethods.SetWindowLongPtrChecked(window, NativeMethods.GwlExStyle, originalExtendedStyle);
            WindowRecoveryJournal.Clear();
            throw new Win32Exception(parentError, "Windows 拒绝附着 Codex 窗口");
        }

        var attachment = new WindowAttachment(
            window,
            host,
            originalParent,
            originalStyle,
            originalExtendedStyle,
            placement);

        attachment.ResizeToHost();
        NativeMethods.SetWindowPos(
            window,
            nint.Zero,
            0,
            0,
            host.ClientSize.Width,
            host.ClientSize.Height,
            NativeMethods.SwpNoZOrder | NativeMethods.SwpFrameChanged | NativeMethods.SwpShowWindow);
        return attachment;
    }

    public void ResizeToHost()
    {
        if (_disposed || !NativeMethods.IsWindow(_window))
        {
            return;
        }

        NativeMethods.MoveWindow(
            _window,
            0,
            0,
            Math.Max(1, _host.ClientSize.Width),
            Math.Max(1, _host.ClientSize.Height),
            true);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        if (!NativeMethods.IsWindow(_window))
        {
            return;
        }

        NativeMethods.SetParent(_window, _originalParent);
        NativeMethods.SetWindowLongPtrChecked(_window, NativeMethods.GwlStyle, _originalStyle);
        NativeMethods.SetWindowLongPtrChecked(_window, NativeMethods.GwlExStyle, _originalExtendedStyle);

        var placement = _originalPlacement;
        NativeMethods.SetWindowPlacement(_window, ref placement);
        NativeMethods.SetWindowPos(
            _window,
            nint.Zero,
            0,
            0,
            0,
            0,
            NativeMethods.SwpNoMove
            | NativeMethods.SwpNoSize
            | NativeMethods.SwpNoZOrder
            | NativeMethods.SwpFrameChanged
            | NativeMethods.SwpShowWindow);
        WindowRecoveryJournal.Clear();
    }
}

internal static class CodexWindowFinder
{
    public static IReadOnlyList<CodexWindowCandidate> FindCandidates()
    {
        var candidates = new List<CodexWindowCandidate>();
        NativeMethods.EnumWindows(
            (window, _) =>
            {
                if (!NativeMethods.IsWindowVisible(window))
                {
                    return true;
                }

                NativeMethods.GetWindowThreadProcessId(window, out var processId);
                if (processId == Environment.ProcessId)
                {
                    return true;
                }

                try
                {
                    using var process = Process.GetProcessById((int)processId);
                    if (!process.ProcessName.Equals("ChatGPT", StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }

                    var title = NativeMethods.GetWindowText(window);
                    var className = NativeMethods.GetClassName(window);
                    var executablePath = process.MainModule?.FileName ?? string.Empty;
                    if (!IsOfficialExecutable(executablePath)
                        || string.IsNullOrWhiteSpace(title)
                        || !className.Equals("Chrome_WidgetWin_1", StringComparison.Ordinal))
                    {
                        return true;
                    }

                    NativeMethods.GetVisibleWindowRect(window, out var rect);
                    var area = Math.Max(0, rect.Right - rect.Left) * Math.Max(0, rect.Bottom - rect.Top);
                    if (area < 200_000)
                    {
                        return true;
                    }

                    candidates.Add(
                        new CodexWindowCandidate(
                            window,
                            (int)processId,
                            title,
                            className,
                            executablePath,
                            area));
                }
                catch (ArgumentException)
                {
                    // The process exited between EnumWindows and inspection.
                }
                catch (InvalidOperationException)
                {
                    // The process exited between EnumWindows and inspection.
                }
                catch (Win32Exception)
                {
                    // A higher-integrity process cannot be inspected safely.
                }

                return true;
            },
            nint.Zero);

        return candidates
            .OrderByDescending(candidate => candidate.Area)
            .ToArray();
    }

    public static bool IsEligibleWindow(nint window)
    {
        if (!NativeMethods.IsWindow(window) || !NativeMethods.IsWindowVisible(window))
        {
            return false;
        }

        return IsOfficialWindowIdentity(window);
    }

    public static bool IsOfficialWindowIdentity(nint window)
    {
        if (!NativeMethods.IsWindow(window))
        {
            return false;
        }

        NativeMethods.GetWindowThreadProcessId(window, out var processId);
        try
        {
            using var process = Process.GetProcessById((int)processId);
            var executablePath = process.MainModule?.FileName ?? string.Empty;
            return process.ProcessName.Equals("ChatGPT", StringComparison.OrdinalIgnoreCase)
                && IsOfficialExecutable(executablePath)
                && NativeMethods.GetClassName(window).Equals("Chrome_WidgetWin_1", StringComparison.Ordinal)
                && !string.IsNullOrWhiteSpace(NativeMethods.GetWindowText(window));
        }
        catch (ArgumentException)
        {
            return false;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
        catch (Win32Exception)
        {
            return false;
        }
    }

    private static bool IsOfficialExecutable(string executablePath)
    {
        return executablePath.Contains(
                $"{Path.DirectorySeparatorChar}WindowsApps{Path.DirectorySeparatorChar}OpenAI.Codex_",
                StringComparison.OrdinalIgnoreCase)
            && executablePath.EndsWith(
                $"{Path.DirectorySeparatorChar}app{Path.DirectorySeparatorChar}ChatGPT.exe",
                StringComparison.OrdinalIgnoreCase);
    }
}

internal sealed record CodexWindowCandidate(
    nint Handle,
    int ProcessId,
    string Title,
    string ClassName,
    string ExecutablePath,
    int Area);

internal static class MagnetGeometry
{
    public static double Distance(NativeMethods.RectangleRaw window, Rectangle target)
    {
        var horizontal = window.Right < target.Left
            ? target.Left - window.Right
            : window.Left > target.Right
                ? window.Left - target.Right
                : 0;
        var vertical = window.Bottom < target.Top
            ? target.Top - window.Bottom
            : window.Top > target.Bottom
                ? window.Top - target.Bottom
                : 0;
        return Math.Sqrt((horizontal * horizontal) + (vertical * vertical));
    }
}

internal sealed class MagnetOverlay : Form
{
    private readonly Label _message = new()
    {
        Dock = DockStyle.Fill,
        TextAlign = ContentAlignment.MiddleCenter,
        Font = new Font("Microsoft YaHei UI", 14F, FontStyle.Bold),
        ForeColor = Color.White,
    };

    public MagnetOverlay()
    {
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.Manual;
        ShowInTaskbar = false;
        TopMost = true;
        Opacity = 0.18;
        BackColor = Color.FromArgb(33, 120, 83);
        Controls.Add(_message);
    }

    protected override bool ShowWithoutActivation => true;

    protected override CreateParams CreateParams
    {
        get
        {
            var parameters = base.CreateParams;
            parameters.ExStyle |= NativeMethods.WsExTransparent
                | NativeMethods.WsExNoActivate
                | NativeMethods.WsExToolWindow;
            return parameters;
        }
    }

    public void ShowPreview(Rectangle bounds, bool armed)
    {
        Bounds = bounds;
        BackColor = armed ? Color.FromArgb(22, 133, 88) : Color.FromArgb(170, 126, 30);
        Opacity = armed ? 0.22 : 0.12;
        _message.Text = armed ? "松开以附着官方 Codex" : "进入磁吸范围";
        if (!Visible)
        {
            Show();
        }

        NativeMethods.SetWindowPos(
            Handle,
            NativeMethods.HwndTopMost,
            bounds.Left,
            bounds.Top,
            bounds.Width,
            bounds.Height,
            NativeMethods.SwpNoActivate | NativeMethods.SwpShowWindow);
    }
}

internal enum MagneticWindowEventKind
{
    MoveStarted,
    LocationChanged,
    MoveEnded,
}

internal sealed record MagneticWindowEvent(MagneticWindowEventKind Kind, nint Window);

internal sealed class MagneticWindowMonitor : IDisposable
{
    private readonly NativeMethods.WinEventProc _callback;
    private readonly Action<MagneticWindowEvent> _sink;
    private readonly nint _moveHook;
    private readonly nint _locationHook;
    private bool _disposed;

    public MagneticWindowMonitor(int processId, Action<MagneticWindowEvent> sink)
    {
        _sink = sink;
        _callback = HandleEvent;
        var flags = NativeMethods.WinEventOutOfContext | NativeMethods.WinEventSkipOwnProcess;
        _moveHook = NativeMethods.SetWinEventHook(
            NativeMethods.EventSystemMoveSizeStart,
            NativeMethods.EventSystemMoveSizeEnd,
            nint.Zero,
            _callback,
            (uint)processId,
            0,
            flags);
        _locationHook = NativeMethods.SetWinEventHook(
            NativeMethods.EventObjectLocationChange,
            NativeMethods.EventObjectLocationChange,
            nint.Zero,
            _callback,
            (uint)processId,
            0,
            flags);

        if (_moveHook == nint.Zero || _locationHook == nint.Zero)
        {
            Dispose();
            throw new Win32Exception(Marshal.GetLastWin32Error(), "无法监听 Codex 窗口拖动事件");
        }
    }

    private void HandleEvent(
        nint hook,
        uint eventId,
        nint window,
        int objectId,
        int childId,
        uint eventThread,
        uint eventTime)
    {
        if (_disposed || window == nint.Zero)
        {
            return;
        }

        var root = NativeMethods.GetAncestor(window, NativeMethods.GaRoot);
        if (root != nint.Zero)
        {
            window = root;
        }

        var kind = eventId switch
        {
            NativeMethods.EventSystemMoveSizeStart => MagneticWindowEventKind.MoveStarted,
            NativeMethods.EventSystemMoveSizeEnd => MagneticWindowEventKind.MoveEnded,
            NativeMethods.EventObjectLocationChange when objectId == NativeMethods.ObjIdWindow
                => MagneticWindowEventKind.LocationChanged,
            _ => (MagneticWindowEventKind?)null,
        };
        if (kind is not null)
        {
            _sink(new MagneticWindowEvent(kind.Value, window));
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        if (_moveHook != nint.Zero)
        {
            NativeMethods.UnhookWinEvent(_moveHook);
        }
        if (_locationHook != nint.Zero)
        {
            NativeMethods.UnhookWinEvent(_locationHook);
        }
    }
}

internal static class WindowRecoveryJournal
{
    private static readonly string JournalPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "AgentDockSpike",
        "window-recovery.json");

    public static void Save(
        nint window,
        int processId,
        nint parent,
        nint style,
        nint extendedStyle,
        NativeMethods.WindowPlacement placement)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(JournalPath)!);
        var state = new RecoveryState(
            window.ToInt64(),
            processId,
            parent.ToInt64(),
            style.ToInt64(),
            extendedStyle.ToInt64(),
            placement.Flags,
            placement.ShowCommand,
            placement.MinimumPosition.X,
            placement.MinimumPosition.Y,
            placement.MaximumPosition.X,
            placement.MaximumPosition.Y,
            placement.NormalPosition.Left,
            placement.NormalPosition.Top,
            placement.NormalPosition.Right,
            placement.NormalPosition.Bottom);
        File.WriteAllText(
            JournalPath,
            JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true }),
            Encoding.UTF8);
    }

    public static void TryRestore()
    {
        if (!File.Exists(JournalPath))
        {
            return;
        }

        try
        {
            var state = JsonSerializer.Deserialize<RecoveryState>(
                File.ReadAllText(JournalPath, Encoding.UTF8));
            if (state is null)
            {
                return;
            }

            var window = new nint(state.Window);
            if (!NativeMethods.IsWindow(window))
            {
                return;
            }

            NativeMethods.GetWindowThreadProcessId(window, out var processId);
            if (processId != state.ProcessId || !CodexWindowFinder.IsOfficialWindowIdentity(window))
            {
                return;
            }

            NativeMethods.SetParent(window, new nint(state.Parent));
            NativeMethods.SetWindowLongPtrChecked(window, NativeMethods.GwlStyle, new nint(state.Style));
            NativeMethods.SetWindowLongPtrChecked(
                window,
                NativeMethods.GwlExStyle,
                new nint(state.ExtendedStyle));
            var placement = NativeMethods.WindowPlacement.Create();
            placement.Flags = state.PlacementFlags;
            placement.ShowCommand = state.ShowCommand;
            placement.MinimumPosition = new Point(state.MinimumX, state.MinimumY);
            placement.MaximumPosition = new Point(state.MaximumX, state.MaximumY);
            placement.NormalPosition = new NativeMethods.RectangleRaw
            {
                Left = state.Left,
                Top = state.Top,
                Right = state.Right,
                Bottom = state.Bottom,
            };
            NativeMethods.SetWindowPlacement(window, ref placement);
            NativeMethods.SetWindowPos(
                window,
                nint.Zero,
                0,
                0,
                0,
                0,
                NativeMethods.SwpNoMove
                | NativeMethods.SwpNoSize
                | NativeMethods.SwpNoZOrder
                | NativeMethods.SwpFrameChanged
                | NativeMethods.SwpShowWindow);
        }
        catch (JsonException)
        {
            // Ignore an incomplete journal and clear it below.
        }
        catch (Win32Exception)
        {
            // Recovery remains best effort; the official window stays untouched.
        }
        finally
        {
            Clear();
        }
    }

    public static void Clear()
    {
        if (File.Exists(JournalPath))
        {
            File.Delete(JournalPath);
        }
    }

    private sealed record RecoveryState(
        long Window,
        int ProcessId,
        long Parent,
        long Style,
        long ExtendedStyle,
        uint PlacementFlags,
        uint ShowCommand,
        int MinimumX,
        int MinimumY,
        int MaximumX,
        int MaximumY,
        int Left,
        int Top,
        int Right,
        int Bottom);
}

internal static class NativeMethods
{
    internal const int GwlStyle = -16;
    internal const int GwlExStyle = -20;
    internal const uint GwOwner = 4;

    internal const long WsChild = 0x40000000L;
    internal const long WsVisible = 0x10000000L;
    internal const long WsCaption = 0x00C00000L;
    internal const long WsThickFrame = 0x00040000L;
    internal const long WsMinimizeBox = 0x00020000L;
    internal const long WsMaximizeBox = 0x00010000L;
    internal const long WsSysMenu = 0x00080000L;
    internal const long WsExAppWindow = 0x00040000L;
    internal const int WsExTransparent = 0x00000020;
    internal const int WsExToolWindow = 0x00000080;
    internal const int WsExNoActivate = 0x08000000;
    internal const long TopLevelWindowStyleMask =
        WsCaption | WsThickFrame | WsMinimizeBox | WsMaximizeBox | WsSysMenu;

    internal static readonly nint HwndTopMost = new(-1);
    internal const uint SwpNoSize = 0x0001;
    internal const uint SwpNoMove = 0x0002;
    internal const uint SwpNoZOrder = 0x0004;
    internal const uint SwpNoActivate = 0x0010;
    internal const uint SwpShowWindow = 0x0040;
    internal const uint SwpFrameChanged = 0x0020;
    internal const uint WmNcLButtonDown = 0x00A1;
    internal const int HtCaption = 2;
    internal const uint GaRoot = 2;
    internal const uint EventSystemMoveSizeStart = 0x000A;
    internal const uint EventSystemMoveSizeEnd = 0x000B;
    internal const uint EventObjectLocationChange = 0x800B;
    internal const uint WinEventOutOfContext = 0x0000;
    internal const uint WinEventSkipOwnProcess = 0x0002;
    internal const int ObjIdWindow = 0;
    internal const uint DwmwaExtendedFrameBounds = 9;

    internal delegate bool EnumWindowsProc(nint window, nint parameter);
    internal delegate void WinEventProc(
        nint hook,
        uint eventId,
        nint window,
        int objectId,
        int childId,
        uint eventThread,
        uint eventTime);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool EnumWindows(EnumWindowsProc callback, nint parameter);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool IsWindow(nint window);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool IsWindowVisible(nint window);

    [DllImport("user32.dll")]
    internal static extern nint GetWindow(nint window, uint command);

    [DllImport("user32.dll")]
    internal static extern nint GetAncestor(nint window, uint flags);

    [DllImport("user32.dll")]
    internal static extern uint GetWindowThreadProcessId(nint window, out uint processId);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern nint SetWinEventHook(
        uint eventMin,
        uint eventMax,
        nint module,
        WinEventProc callback,
        uint processId,
        uint threadId,
        uint flags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool UnhookWinEvent(nint hook);

    [DllImport("user32.dll", EntryPoint = "GetWindowTextW", CharSet = CharSet.Unicode)]
    private static extern int GetWindowTextRaw(nint window, StringBuilder text, int maximumCount);

    [DllImport("user32.dll", EntryPoint = "GetClassNameW", CharSet = CharSet.Unicode)]
    private static extern int GetClassNameRaw(nint window, StringBuilder className, int maximumCount);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetWindowRect(nint window, out RectangleRaw rectangle);

    [DllImport("user32.dll")]
    internal static extern nint GetParent(nint window);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern nint SetParent(nint child, nint newParent);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    internal static extern nint GetWindowLongPtr(nint window, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern nint SetWindowLongPtrRaw(nint window, int index, nint value);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool MoveWindow(nint window, int x, int y, int width, int height, [MarshalAs(UnmanagedType.Bool)] bool repaint);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetWindowPlacement(nint window, ref WindowPlacement placement);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetWindowPlacement(nint window, ref WindowPlacement placement);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetWindowPos(
        nint window,
        nint insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    internal static extern nint SendMessage(nint window, uint message, nint wParam, nint lParam);

    [DllImport("dwmapi.dll")]
    private static extern int DwmGetWindowAttribute(
        nint window,
        uint attribute,
        out RectangleRaw value,
        uint valueSize);

    internal static string GetWindowText(nint window)
    {
        var text = new StringBuilder(1024);
        _ = GetWindowTextRaw(window, text, text.Capacity);
        return text.ToString();
    }

    internal static string GetClassName(nint window)
    {
        var className = new StringBuilder(256);
        _ = GetClassNameRaw(window, className, className.Capacity);
        return className.ToString();
    }

    internal static bool GetVisibleWindowRect(nint window, out RectangleRaw rectangle)
    {
        var result = DwmGetWindowAttribute(
            window,
            DwmwaExtendedFrameBounds,
            out rectangle,
            (uint)Marshal.SizeOf<RectangleRaw>());
        return result == 0 || GetWindowRect(window, out rectangle);
    }

    internal static void SetWindowLongPtrChecked(nint window, int index, nint value)
    {
        Marshal.SetLastPInvokeError(0);
        var previous = SetWindowLongPtrRaw(window, index, value);
        var error = Marshal.GetLastWin32Error();
        if (previous == nint.Zero && error != 0)
        {
            throw new Win32Exception(error);
        }
    }

    internal static void ThrowIfFalse(bool result, string message)
    {
        if (!result)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), message);
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct RectangleRaw
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct WindowPlacement
    {
        public uint Length;
        public uint Flags;
        public uint ShowCommand;
        public Point MinimumPosition;
        public Point MaximumPosition;
        public RectangleRaw NormalPosition;

        public static WindowPlacement Create() => new()
        {
            Length = (uint)Marshal.SizeOf<WindowPlacement>(),
        };
    }
}
