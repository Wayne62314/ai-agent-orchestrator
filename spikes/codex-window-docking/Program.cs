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
        Text = "这里是官方 Codex 客户端的窗口插槽\r\n\r\n先打开 Codex，再点击“附着真实 Codex 窗口”",
        ForeColor = Color.FromArgb(86, 96, 91),
        Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Regular),
    };

    private WindowAttachment? _attachment;
    private readonly string? _smokeOutput;

    public DockingForm(string? smokeOutput = null)
    {
        _smokeOutput = smokeOutput;
        Text = "Agent Dock — 真实 Codex 窗口附着验证";
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

        _hostPanel.Controls.Add(_emptyState);
        Controls.Add(_hostPanel);
        Controls.Add(toolbar);

        _attachButton.Click += (_, _) => AttachCodex();
        _detachButton.Click += (_, _) => DetachCodex();
        _openButton.Click += (_, _) => OpenCodexTask();
        _hostPanel.Resize += (_, _) => _attachment?.ResizeToHost();
        FormClosing += (_, _) => DetachCodex();

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

            _attachment = WindowAttachment.Attach(candidate.Handle, _hostPanel);
            _emptyState.Visible = false;
            _attachButton.Enabled = false;
            _detachButton.Enabled = true;
            SetStatus($"已附着官方 Codex（进程 {candidate.ProcessId}）", isError: false);
        }
        catch (Exception exception)
        {
            _attachment = null;
            _emptyState.Visible = true;
            SetStatus($"附着失败：{exception.Message}", isError: true);
        }
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

    public static WindowAttachment Attach(nint window, Control host)
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
                if (!NativeMethods.IsWindowVisible(window)
                    || NativeMethods.GetWindow(window, NativeMethods.GwOwner) != nint.Zero)
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
                    if (string.IsNullOrWhiteSpace(title))
                    {
                        return true;
                    }

                    NativeMethods.GetWindowRect(window, out var rect);
                    candidates.Add(
                        new CodexWindowCandidate(
                            window,
                            (int)processId,
                            title,
                            className,
                            Math.Max(0, rect.Right - rect.Left) * Math.Max(0, rect.Bottom - rect.Top)));
                }
                catch (ArgumentException)
                {
                    // The process exited between EnumWindows and inspection.
                }

                return true;
            },
            nint.Zero);

        return candidates
            .OrderByDescending(candidate => candidate.Area)
            .ToArray();
    }
}

internal sealed record CodexWindowCandidate(
    nint Handle,
    int ProcessId,
    string Title,
    string ClassName,
    int Area);

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
    internal const long TopLevelWindowStyleMask =
        WsCaption | WsThickFrame | WsMinimizeBox | WsMaximizeBox | WsSysMenu;

    internal const uint SwpNoSize = 0x0001;
    internal const uint SwpNoMove = 0x0002;
    internal const uint SwpNoZOrder = 0x0004;
    internal const uint SwpShowWindow = 0x0040;
    internal const uint SwpFrameChanged = 0x0020;

    internal delegate bool EnumWindowsProc(nint window, nint parameter);

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
    internal static extern uint GetWindowThreadProcessId(nint window, out uint processId);

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
