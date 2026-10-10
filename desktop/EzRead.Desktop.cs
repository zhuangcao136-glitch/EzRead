// Native EzRead window. WebView2 renders the existing local UI; no browser app window.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal sealed class ShellOptions
{
    public string Root, Data, Url, SmokeReport, VerificationSession, AppId, Identity, StartupReport;
    public bool Child;
    public Uri Origin;
    public static ShellOptions Parse(string[] args)
    {
        var values = new Dictionary<string, string>();
        for (int i = 0; i < args.Length; i += 2) {
            if (i + 1 >= args.Length || (args[i] != "--app-root" && args[i] != "--data-dir" && args[i] != "--url" && args[i] != "--smoke-test" && args[i] != "--verification-session" && args[i] != "--startup-report"))
                throw new ArgumentException("Invalid desktop startup arguments.");
            values.Add(args[i], args[i + 1]);
        }
        var result = new ShellOptions();
        result.Root = Path.GetFullPath(values["--app-root"]);
        result.Data = Path.GetFullPath(values["--data-dir"]);
        result.Url = values["--url"];
        result.Origin = new Uri(result.Url);
        if (result.Origin.Scheme != "http" || result.Origin.Host != "127.0.0.1" || !String.IsNullOrEmpty(result.Origin.UserInfo))
            throw new ArgumentException("EzRead only loads its local loopback service.");
        if (values.ContainsKey("--smoke-test")) result.SmokeReport = Path.GetFullPath(values["--smoke-test"]);
        if (values.ContainsKey("--startup-report")) result.StartupReport = Path.GetFullPath(values["--startup-report"]);
        if (values.ContainsKey("--verification-session")) {
            Guid session;
            if (!Guid.TryParse(values["--verification-session"], out session)) throw new ArgumentException("Invalid verification session.");
            result.VerificationSession = session.ToString("N");
        }
        return result;
    }
    public bool IsLocal(string url) {
        Uri uri;
        return Uri.TryCreate(url, UriKind.Absolute, out uri) && uri.Scheme == Origin.Scheme && uri.Host == Origin.Host && uri.Port == Origin.Port && String.IsNullOrEmpty(uri.UserInfo);
    }
}

internal static class Program
{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);
    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try {
            if (args.Length == 0) return BootstrapLauncher();
            var options = ShellOptions.Parse(args);
            string identity;
            using (var hash = SHA256.Create()) identity = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(options.Root.ToUpperInvariant() + "|" + options.Data.ToUpperInvariant() + "|" + options.Origin.GetLeftPart(UriPartial.Authority)))).Replace("-", "").Substring(0, 24);
            options.Identity = identity;
            options.AppId = options.VerificationSession != null || options.SmokeReport != null
                ? "EzRead.Desktop.Verification." + (options.VerificationSession ?? identity) : "EzRead.Desktop";
            Marshal.ThrowExceptionForHR(SetCurrentProcessExplicitAppUserModelID(options.AppId));
            using (var mutex = new Mutex(false, "Local\\EzRead.Desktop." + identity)) {
                bool owned;
                try { owned = mutex.WaitOne(0); } catch (AbandonedMutexException) { owned = true; }
                if (!owned) return NativeOpenRequest.Deliver(identity);
                NativeProcessLifetime.Attach();
                try {
                    using (var form = new ReaderWindow(options)) {
                        Application.Run(form);
                        return form.ExitCode;
                    }
                } finally { mutex.ReleaseMutex(); }
            }
        } catch (Exception exc) {
            if (Array.IndexOf(args, "--startup-report") >= 0) return 1;
            int reportIndex = Array.IndexOf(args, "--smoke-test");
            if (reportIndex >= 0 && reportIndex + 1 < args.Length) {
                File.WriteAllText(args[reportIndex + 1], new JavaScriptSerializer().Serialize(new { error = exc.ToString() }), Encoding.UTF8);
                return 1;
            }
            MessageBox.Show("EzRead 无法打开桌面窗口。\n" + exc.Message + "\n请从应用目录的启动器打开，并确认 WebView2 Runtime 已安装。", "EzRead", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
    private static int BootstrapLauncher()
    {
        string root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
        string entry = Path.Combine(root, "launch.pyw");
        if (!File.Exists(entry)) throw new FileNotFoundException("Please keep the complete EzRead application folder together.");
        var candidates = new List<string>();
        string configured = Environment.GetEnvironmentVariable("EZREAD_PYTHON");
        if (!String.IsNullOrEmpty(configured)) candidates.Add(configured);
        candidates.Add(Path.Combine(root, "runtime", "pythonw.exe"));
        candidates.Add(Path.Combine(root, ".venv", "Scripts", "pythonw.exe"));
        candidates.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "pythonw.exe"));
        string installs = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python");
        if (Directory.Exists(installs)) foreach (string directory in Directory.GetDirectories(installs)) candidates.Add(Path.Combine(directory, "pythonw.exe"));
        foreach (string directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator)) {
            if (directory.Length > 0 && directory.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) < 0) candidates.Add(Path.Combine(directory, "pythonw.exe"));
        }
        foreach (string runtime in candidates) {
            if (!File.Exists(runtime)) continue;
            Process.Start(new ProcessStartInfo(runtime, "\"" + entry + "\"") { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true });
            return 0;
        }
        throw new FileNotFoundException("Python runtime not found. See README.md or set EZREAD_PYTHON.");
    }
}

internal static class NativeOpenRequest
{
    private delegate bool EnumWindow(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern uint RegisterWindowMessage(string name);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindow callback, IntPtr parameter);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr GetProp(IntPtr window, string name);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern bool SetProp(IntPtr window, string name, IntPtr value);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr RemoveProp(IntPtr window, string name);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr SendMessageTimeout(IntPtr window, uint message, IntPtr wparam, IntPtr lparam, uint flags, uint timeout, out UIntPtr result);
    public static readonly uint Message = RegisterWindowMessage("EzRead.Desktop.Open.v1");
    private static string Property(string identity) { return "EzRead.Desktop.Instance." + identity; }
    public static void Register(IntPtr window, string identity) {
        if (!SetProp(window, Property(identity), new IntPtr(1))) throw new InvalidOperationException("Cannot register the desktop instance.");
    }
    public static void Unregister(IntPtr window, string identity) { RemoveProp(window, Property(identity)); }
    public static int Deliver(string identity) {
        IntPtr target = IntPtr.Zero;
        EnumWindows(delegate(IntPtr window, IntPtr unused) {
            if (GetProp(window, Property(identity)) == IntPtr.Zero) return true;
            target = window; return false;
        }, IntPtr.Zero);
        if (target == IntPtr.Zero) return 3; // The owning instance is starting or destroying its HWND.
        UIntPtr answer;
        if (SendMessageTimeout(target, Message, IntPtr.Zero, IntPtr.Zero, 0x22, 2000, out answer) == IntPtr.Zero)
            throw new TimeoutException("The existing desktop did not acknowledge the open request.");
        return answer.ToUInt64() == 1 ? 0 : 3;
    }
}

internal static class NativeProcessLifetime
{
    [StructLayout(LayoutKind.Sequential)] private struct BasicLimit {
        public long ProcessTime, JobTime; public uint Flags; public UIntPtr Minimum, Maximum;
        public uint ActiveProcesses; public UIntPtr Affinity; public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters {
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimit {
        public BasicLimit Basic; public IoCounters Io; public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int type, ref ExtendedLimit limits, uint size);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
    private static IntPtr job;
    public static void Attach() {
        if (job != IntPtr.Zero) return;
        IntPtr handle = CreateJobObject(IntPtr.Zero, null);
        var limits = new ExtendedLimit(); limits.Basic.Flags = 0x2000 | 0x800;
        if (handle == IntPtr.Zero || !SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(typeof(ExtendedLimit))) || !AssignProcessToJobObject(handle, GetCurrentProcess())) {
            int error = Marshal.GetLastWin32Error(); if (handle != IntPtr.Zero) CloseHandle(handle);
            throw new System.ComponentModel.Win32Exception(error, "无法建立 EzRead 子进程退出管理。");
        }
        // Keep this handle until process exit. Closing it early also kills us.
        job = handle;
    }
}

internal static class NativeTaskbar
{
    [ComImport, Guid("56FDF342-FD6D-11D0-958A-006097C9A090"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface ITaskbarList {
        [PreserveSig] int HrInit();
        [PreserveSig] int AddTab(IntPtr window);
        [PreserveSig] int DeleteTab(IntPtr window);
        [PreserveSig] int ActivateTab(IntPtr window);
        [PreserveSig] int SetActiveAlt(IntPtr window);
    }
    [ComImport, Guid("56FDF344-FD6D-11D0-958A-006097C9A090")]
    private class TaskbarList { }
    public static int SetVisible(IntPtr window, bool visible) {
        ITaskbarList taskbar = null;
        try {
            taskbar = (ITaskbarList)new TaskbarList();
            int result = taskbar.HrInit();
            return result < 0 ? result : visible ? taskbar.AddTab(window) : taskbar.DeleteTab(window);
        } finally { if (taskbar != null) Marshal.FinalReleaseComObject(taskbar); }
    }
}

internal sealed class OwnedBackend : IDisposable
{
    private readonly ShellOptions options;
    private readonly JavaScriptSerializer json = new JavaScriptSerializer();
    private Process process;
    private string instance;
    private bool attached;
    private readonly object gate = new object();
    public OwnedBackend(ShellOptions value) { options = value; }
    private Dictionary<string, object> Request(string path, string body = null, int timeout = 2000) {
        var request = (HttpWebRequest)WebRequest.Create(new Uri(options.Origin, path));
        request.Proxy = null; request.Timeout = timeout; request.ReadWriteTimeout = timeout;
        // Abort covers DNS, request upload and a response body that never ends.
        using (var timer = new System.Threading.Timer(delegate { request.Abort(); }, null, timeout, Timeout.Infinite)) {
        if (body != null) {
            var bytes = Encoding.UTF8.GetBytes(body); request.Method = "POST";
            request.ContentType = "application/json"; request.ContentLength = bytes.Length;
            request.Headers["Origin"] = options.Origin.GetLeftPart(UriPartial.Authority);
            using (var stream = request.GetRequestStream()) stream.Write(bytes, 0, bytes.Length);
        }
        using (var response = request.GetResponse())
        using (var reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
            return json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
        }
    }
    private static bool SamePath(object value, string expected) {
        try { return value is string && Path.IsPathRooted((string)value) && String.Equals(Path.GetFullPath((string)value).TrimEnd('\\'), Path.GetFullPath(expected).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase); }
        catch { return false; }
    }
    public void Capture() { lock (gate) CaptureCore(); }
    private void CaptureCore() {
        var health = Request("/api/health");
        if (!health.ContainsKey("app_root") || !health.ContainsKey("data_dir") || !health.ContainsKey("pid") || !health.ContainsKey("instance_id") || !SamePath(health["app_root"], options.Root) || !SamePath(health["data_dir"], options.Data))
            throw new InvalidOperationException("后台身份不匹配，未操作该服务。");
        var candidate = Process.GetProcessById(Convert.ToInt32(health["pid"]));
        // Retain the kernel handle; a reused numeric PID can never target another process.
        if (candidate.Handle == IntPtr.Zero) { candidate.Dispose(); throw new InvalidOperationException("后台进程已退出。"); }
        var confirmation = Request("/api/health");
        if (!confirmation.ContainsKey("instance_id") || !Object.Equals(confirmation["instance_id"], health["instance_id"]) || !Object.Equals(confirmation["pid"], health["pid"])) {
            candidate.Dispose(); throw new InvalidOperationException("后台实例已变化。");
        }
        if (process != null) process.Dispose();
        process = candidate; instance = (string)health["instance_id"];
    }
    public void Attach() {
        lock (gate) {
            Request("/api/desktop-attach", json.Serialize(new { instance_id = instance, process_id = Process.GetCurrentProcess().Id }));
            attached = true;
        }
    }
    public void Stop() {
        lock (gate) {
        // Until navigation succeeds this host has only borrowed the backend.
        // A failed startup must not stop a service that was already running.
        if (!attached) return;
        if (process != null && process.HasExited) return;
        // The existing stable handle already identifies the owned backend. Do
        // not spend the shutdown budget repeating three startup HTTP requests.
        if (process == null) { CaptureCore(); if (process.HasExited) return; }
        try { Request("/api/shutdown", json.Serialize(new { instance_id = instance })); } catch (WebException) { }
        if (!process.WaitForExit(7000)) {
            process.Kill();
            if (!process.WaitForExit(3000)) throw new TimeoutException("后台进程未能在期限内退出。");
        }
        }
    }
    public void Dispose() { lock (gate) { if (process != null) process.Dispose(); } }
}

internal sealed class ReaderWindow : Form
{
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr LoadImage(IntPtr instance, string name, uint type, int width, int height, uint flags);
    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern int GetSystemMetricsForDpi(int index, uint dpi);
    [StructLayout(LayoutKind.Sequential)] private struct NativeRect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr window, out NativeRect rect);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr window, out NativeRect rect);
    [DllImport("user32.dll")] private static extern int GetWindowLong(IntPtr window, int index);
    [DllImport("user32.dll")] private static extern bool AdjustWindowRectExForDpi(ref NativeRect rect, uint style, bool menu, uint extendedStyle, uint dpi);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    private readonly ShellOptions options;
    private WebView2 view = new WebView2();
    private readonly Panel viewport = new Panel { Dock = DockStyle.Fill };
    private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
    private readonly Icon captionIcon;
    private readonly System.Windows.Forms.Timer viewportTimer = new System.Windows.Forms.Timer { Interval = 100 };
    private const double LayoutWidth = 1920;
    private Size? smokeMonitorSize;
    private Rectangle fittedMonitorArea;
    private double fittedDeviceScale;
    private int fittedMinimumWindowWidth;
    private int viewportOffset;
    private bool viewportModal;
    private int viewportOffsetBeforeModal;
    private bool fittingViewport;
    private bool ready, preparingClose, mayClose, webviewFailed, startupReported;
    private readonly OwnedBackend backend;
    public int ExitCode;
    public ReaderWindow(ShellOptions value)
    {
        options = value;
        viewport.AutoScroll = options.Child;
        if (!options.Child) backend = new OwnedBackend(options);
        Text = "\u200b"; AccessibleName = "EzRead";
        Icon = new Icon(Path.Combine(options.Root, "static", "ezread.ico"), 32, 32);
        captionIcon = new Icon(Path.Combine(options.Root, "static", "window-icon-transparent.ico"), 16, 16);
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(1450, 920);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(244, 247, 239);
        RestoreBoundsFromDisk();
        UpdateMinimumSize(MonitorArea());
        view.Location = Point.Empty; view.DefaultBackgroundColor = BackColor;
        viewport.Controls.Add(view); Controls.Add(viewport);
        viewport.SizeChanged += delegate { RequestViewportFit(); };
        LocationChanged += delegate { if (options.SmokeReport == null) RequestViewportFit(); };
        viewportTimer.Tick += async delegate { viewportTimer.Stop(); await FitViewport(); };
        if (options.SmokeReport != null || options.VerificationSession != null) ShowInTaskbar = false;
        if (options.SmokeReport != null) { StartPosition = FormStartPosition.Manual; Location = new Point(-20000, -20000); }
        Log("window-mode app-id=" + options.AppId + " taskbar=" + ShowInTaskbar);
        Shown += async delegate { await InitializeWebView(); };
        FormClosing += OnClosing;
        FormClosed += delegate { RemoveOwnTaskbarButton(); Log("closed"); if (backend != null) backend.Dispose(); };
    }
    protected override void WndProc(ref Message message)
    {
        if (message.Msg == NativeOpenRequest.Message) {
            bool accepted = ready && !ClosingWindow;
            if (accepted) ActivateExisting();
            else Log("open-request-deferred");
            message.Result = new IntPtr(accepted ? 1 : 3); return;
        }
        // Only our own caption icon is transparent. The real large icon belongs
        // to this executable/Form and lives for the entire window lifetime.
        if (captionIcon != null && message.Msg == 0x80 && message.WParam == IntPtr.Zero) message.LParam = captionIcon.Handle;
        if (message.Msg == 0x80 && message.WParam == new IntPtr(1) && Icon != null) message.LParam = Icon.Handle;
        if (captionIcon != null && message.Msg == 0x7f && (message.WParam == IntPtr.Zero || message.WParam == new IntPtr(2))) { message.Result = captionIcon.Handle; return; }
        base.WndProc(ref message);
    }
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        if (!options.Child) NativeOpenRequest.Register(Handle, options.Identity);
        if (captionIcon != null) SendMessage(Handle, 0x80, IntPtr.Zero, captionIcon.Handle);
        if (Icon != null) SendMessage(Handle, 0x80, new IntPtr(1), Icon.Handle);
    }
    protected override void OnHandleDestroyed(EventArgs e) {
        if (!options.Child) NativeOpenRequest.Unregister(Handle, options.Identity);
        base.OnHandleDestroyed(e);
    }
    protected override bool ShowWithoutActivation { get { return options.SmokeReport != null || options.VerificationSession != null; } }
    private bool ClosingWindow { get { var owner = Owner as ReaderWindow; return preparingClose || mayClose || IsDisposed || Disposing || owner != null && owner.ClosingWindow; } }
    public void ActivateExisting() {
        if (ClosingWindow) return;
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        Show(); if (options.SmokeReport == null) Activate();
    }
    private void RequestViewportFit() {
        if (!ready || ClosingWindow) return;
        viewportTimer.Stop(); viewportTimer.Start();
    }
    private Rectangle MonitorArea() {
        return smokeMonitorSize.HasValue ? new Rectangle(Point.Empty, smokeMonitorSize.Value) : Screen.FromControl(this).WorkingArea;
    }
    private void UpdateMinimumSize(Rectangle area) {
        int width = Math.Max((area.Width + 1) / 2, area == fittedMonitorArea ? fittedMinimumWindowWidth : 0);
        var minimum = new Size(width, (area.Height + 1) / 2);
        if (MinimumSize != minimum) MinimumSize = minimum;
        // Use physical geometry: legacy WinForms caches can retain the previous
        // monitor's non-client size after a DPI change or programmatic resize.
        NativeRect bounds;
        if (WindowState == FormWindowState.Normal && GetWindowRect(Handle, out bounds) && bounds.Right - bounds.Left < minimum.Width)
            SetWindowPos(Handle, IntPtr.Zero, 0, 0, minimum.Width, bounds.Bottom - bounds.Top, 0x16);
    }
    private int WindowScrollBarWidth() {
        try { return Math.Max(1, GetSystemMetricsForDpi(2, GetDpiForWindow(Handle))); }
        catch (EntryPointNotFoundException) { return SystemInformation.VerticalScrollBarWidth; }
    }
    private Size WindowFrameSize() {
        try {
            var rect = new NativeRect();
            uint style = unchecked((uint)GetWindowLong(Handle, -16)) & ~0x01000000u;
            if (AdjustWindowRectExForDpi(ref rect, style, false, unchecked((uint)GetWindowLong(Handle, -20)), GetDpiForWindow(Handle)))
                return new Size(rect.Right - rect.Left, rect.Bottom - rect.Top);
        } catch (EntryPointNotFoundException) { }
        return SizeFromClientSize(Size.Empty);
    }
    private Size ViewportClientSize() {
        NativeRect rect;
        return GetClientRect(viewport.Handle, out rect) ? new Size(rect.Right - rect.Left, rect.Bottom - rect.Top) : viewport.ClientSize;
    }
    private int ViewportViewWidth() {
        NativeRect bounds; var client = ViewportClientSize();
        if (!options.Child) return Math.Max(1, client.Width);
        int width = GetWindowRect(viewport.Handle, out bounds) ? bounds.Right - bounds.Left : viewport.Width;
        return Math.Max(1, Math.Min(client.Width, width - WindowScrollBarWidth()));
    }
    private void SetViewportOffset(int offset) {
        if (options.Child) return;
        int maximum = Math.Max(0, view.Height - ViewportClientSize().Height);
        viewportOffset = Math.Max(0, Math.Min(maximum, offset));
        view.Location = new Point(0, -viewportOffset);
    }
    private void ReceiveViewportMessage(string message) {
        try {
            object packet = json.DeserializeObject(message);
            if (packet as string == "ezread-fit-viewport") { RequestViewportFit(); return; }
            var wheel = packet as Dictionary<string, object>;
            if (wheel == null || !wheel.ContainsKey("type") || wheel["type"] as string != "ezread-sidebar-wheel" || !wheel.ContainsKey("deltaY") || !wheel.ContainsKey("pixelRatio")) return;
            double delta = Convert.ToDouble(wheel["deltaY"]), ratio = Convert.ToDouble(wheel["pixelRatio"]);
            if (double.IsNaN(delta) || double.IsInfinity(delta) || Math.Abs(delta) > 20000 || double.IsNaN(ratio) || double.IsInfinity(ratio) || ratio <= 0 || ratio > 16) return;
            SetViewportOffset(viewportOffset + (int)Math.Round(delta * ratio));
        } catch (ArgumentException) { }
        catch (FormatException) { }
        catch (InvalidCastException) { }
        catch (OverflowException) { }
    }
    private async Task FitViewport() {
        if (!ready || ClosingWindow || WindowState == FormWindowState.Minimized) return;
        if (fittingViewport) { RequestViewportFit(); return; }
        fittingViewport = true;
        try {
            var area = MonitorArea(); UpdateMinimumSize(area);
            var frame = WindowFrameSize();
            int scrollbarWidth = options.Child ? WindowScrollBarWidth() : 0;
            // The main window clips a fixed-height page without native bars.
            int fullWidth = Math.Max(1, area.Width - frame.Width - scrollbarWidth), fullHeight = Math.Max(1, area.Height - frame.Height);
            if (options.Child) {
                view.ZoomFactor = 1;
                view.Size = new Size(ViewportViewWidth(), fullHeight);
                return;
            }
            string value = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify(ezreadDesktopViewportMetrics())");
            if (ClosingWindow) return;
            var metrics = json.Deserialize<Dictionary<string, object>>(json.Deserialize<string>(value));
            bool modal = metrics.ContainsKey("dialogOpen") && Convert.ToBoolean(metrics["dialogOpen"]);
            if (modal != viewportModal) {
                if (modal) viewportOffsetBeforeModal = viewportOffset;
                else viewportOffset = viewportOffsetBeforeModal;
                viewportModal = modal;
            }
            int pageHeight = modal ? Math.Max(1, Math.Min(fullHeight, ViewportClientSize().Height)) : fullHeight;
            double gutter = Convert.ToDouble(metrics["gutter"]);
            double deviceScale = Convert.ToDouble(metrics["pixelRatio"]) / view.ZoomFactor;
            // Use the monitor's longer axis, so portrait screens keep readable
            // cards and fewer columns. Window resize never determines page zoom.
            if (area != fittedMonitorArea || Math.Abs(deviceScale - fittedDeviceScale) > .0001) {
                // Calibrate from monitor dimensions and DPI without widening
                // the WebView beyond this window; normal resizes keep its zoom.
                view.Size = new Size(ViewportViewWidth(), pageHeight);
                await Task.Delay(75);
                if (ClosingWindow) return;
                for (int attempt = 0; attempt < 4; attempt++) {
                    value = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify(ezreadDesktopViewportMetrics())");
                    if (ClosingWindow) return;
                    metrics = json.Deserialize<Dictionary<string, object>>(json.Deserialize<string>(value));
                    gutter = Convert.ToDouble(metrics["gutter"]);
                    double target = Math.Max(.25, Math.Min(5, Math.Max(fullWidth, fullHeight) / deviceScale / (LayoutWidth + gutter)));
                    if (Math.Abs(target - view.ZoomFactor) <= .0001) break;
                    view.ZoomFactor = target;
                    await Task.Delay(75);
                    if (ClosingWindow) return;
                }
                fittedMonitorArea = area; fittedDeviceScale = deviceScale;
            }
            double zoom = view.ZoomFactor;
            int minimumWidth = (int)Math.Ceiling((Convert.ToDouble(metrics["minimumWidth"]) + gutter) * deviceScale * zoom);
            // Enforce two complete columns at the native window boundary. The
            // page always fits horizontally, so no horizontal pan is needed.
            fittedMinimumWindowWidth = minimumWidth + frame.Width + scrollbarWidth;
            UpdateMinimumSize(area);
            // The panel's client width is authoritative at every monitor DPI;
            // system-wide scrollbar metrics can leave a few pixels of overflow.
            var size = new Size(ViewportViewWidth(), pageHeight);
            if (view.Size != size) view.Size = size;
            SetViewportOffset(viewportOffset);
        } catch (Exception exc) { Log("viewport-fit: " + exc.GetType().Name); }
        finally { fittingViewport = false; }
    }
    private async Task InitializeWebView(bool recovering = false)
    {
        try {
            Log("initializing");
            if (backend != null) {
                try { await Task.Run((Action)backend.Capture); }
                catch { if (options.SmokeReport == null) throw; }
            }
            if (ClosingWindow && !recovering) return;
            CoreWebView2Environment.GetAvailableBrowserVersionString();
            Log("runtime-detected");
            string profile = Path.Combine(options.Data, "webview2-profile");
            // Catch a blocked profile before WebView2 can show its own system dialog.
            if (File.Exists(profile)) throw new IOException("浏览器数据目录被同名文件占用。");
            Directory.CreateDirectory(profile);
            var environment = await CoreWebView2Environment.CreateAsync(null, profile);
            if (ClosingWindow && !recovering) return;
            Log("environment-created");
            await view.EnsureCoreWebView2Async(environment);
            if (ClosingWindow && !recovering) return;
            webviewFailed = false;
            Log("control-created");
            var core = view.CoreWebView2;
            core.Settings.AreDefaultContextMenusEnabled = true;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsBuiltInErrorPageEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsZoomControlEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            // No remote debugging port, host objects, or certificate overrides.
            await core.AddScriptToExecuteOnDocumentCreatedAsync(MigrationScript());
            if (ClosingWindow && !recovering) return;
            if (options.SmokeReport != null) await core.AddScriptToExecuteOnDocumentCreatedAsync("(()=>{window.__ezreadSmokeErrors=[];window.addEventListener('error',e=>window.__ezreadSmokeErrors.push(e.message));window.addEventListener('unhandledrejection',e=>window.__ezreadSmokeErrors.push(String(e.reason)));document.addEventListener('DOMContentLoaded',()=>new MutationObserver(changes=>{for(const change of changes)for(const node of change.addedNodes)if(node.classList?.contains('error'))window.__ezreadSmokeErrors.push(node.textContent);}).observe(document.querySelector('#toasts'),{childList:true}),{once:true});})();");
            if (!options.Child) {
                core.WebMessageReceived += delegate(object sender, CoreWebView2WebMessageReceivedEventArgs e) {
                    if (!options.IsLocal(e.Source)) return;
                    Uri source; if (!Uri.TryCreate(e.Source, UriKind.Absolute, out source) || (source.AbsolutePath != "/" && source.AbsolutePath != "/static/index.html")) return;
                    ReceiveViewportMessage(e.WebMessageAsJson);
                };
                await core.AddScriptToExecuteOnDocumentCreatedAsync("(()=>{if(window.top!==window)return;const fit=()=>window.chrome?.webview?.postMessage('ezread-fit-viewport');window.addEventListener('resize',fit);document.addEventListener('DOMContentLoaded',()=>{let font=getComputedStyle(document.documentElement).fontSize;new MutationObserver(()=>{const next=getComputedStyle(document.documentElement).fontSize;if(next!==font){font=next;fit();}}).observe(document.documentElement,{attributes:true,attributeFilter:['style']});new MutationObserver(changes=>{if(changes.some(change=>change.target.tagName==='DIALOG'))fit();}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']});fit();},{once:true});})();");
                view.ZoomFactorChanged += delegate { RequestViewportFit(); };
            }
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) {
                if (options.IsLocal(e.Uri) || e.Uri == "about:blank") return;
                e.Cancel = true;
                if (e.IsUserInitiated) OpenExternal(e.Uri);
            };
            core.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) {
                e.Handled = true;
                if (!e.IsUserInitiated || ClosingWindow) return;
                if (!options.IsLocal(e.Uri)) { OpenExternal(e.Uri); return; }
                var childOptions = new ShellOptions { Root = options.Root, Data = options.Data, Origin = options.Origin, Url = e.Uri, Child = true, VerificationSession = options.VerificationSession, AppId = options.AppId, Identity = options.Identity };
                var child = new ReaderWindow(childOptions);
                child.Show(this);
            };
            core.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs e) {
                e.Handled = true;
                if (ClosingWindow) { e.Cancel = true; return; }
                using (var dialog = new SaveFileDialog()) {
                    dialog.FileName = Path.GetFileName(e.ResultFilePath);
                    dialog.OverwritePrompt = true; dialog.RestoreDirectory = true;
                    if (dialog.ShowDialog(this) != DialogResult.OK) { e.Cancel = true; return; }
                    e.ResultFilePath = dialog.FileName;
                }
            };
            core.ProcessFailed += delegate(object sender, CoreWebView2ProcessFailedEventArgs e) {
                ready = false; webviewFailed = true;
                Log("webview-process-failed kind=" + e.ProcessFailedKind + " closing=" + ClosingWindow);
                if (ClosingWindow) return;
                ShowWarning("阅读窗口遇到问题，请关闭后重新打开。已保存的论文和草稿会保留。", MessageBoxIcon.Warning);
            };
            core.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs e) {
                if (ClosingWindow && !recovering) return;
                if (!e.IsSuccess) {
                    ready = false; Log("navigation-failed: " + e.WebErrorStatus);
                    if (options.StartupReport != null) { ExitCode = 1; WriteStartupReport(false); Close(); }
                    return;
                }
                try {
                    if (backend != null) {
                        try { await Task.Run((Action)backend.Attach); }
                        catch { if (options.SmokeReport == null) throw; }
                    }
                    if (ClosingWindow && !recovering) return;
                    ready = true;
                    Log("ready WebView2=" + environment.BrowserVersionString);
                    WriteStartupReport(true);
                    await FitViewport();
                    if (options.SmokeReport != null) await SmokeTest(environment);
                } catch (Exception exc) {
                    ExitCode = 1; Log("navigation-initialization: " + exc.GetType().Name);
                    WriteStartupReport(false);
                    if (options.StartupReport != null || options.SmokeReport != null) Close();
                    else ShowWarning("阅读窗口初始化失败，请关闭后重试。", MessageBoxIcon.Error, true);
                }
            };
            core.Navigate(options.Url);
        } catch (Exception exc) {
            if (recovering) throw;
            if (ClosingWindow) { Log("initialization-cancelled: " + exc.GetType().Name); return; }
            ExitCode = 1; Log(exc.ToString());
            WriteStartupReport(false);
            if (options.StartupReport != null) { Close(); return; }
            if (options.SmokeReport != null) {
                File.WriteAllText(options.SmokeReport, json.Serialize(new { error = exc.ToString() }), Encoding.UTF8);
                Close(); return;
            }
            ShowWarning("桌面窗口初始化失败：\n" + exc.Message + "\n如提示缺少运行时，请安装 Microsoft Edge WebView2 Runtime。", MessageBoxIcon.Error, true);
        }
    }
    private void WriteStartupReport(bool success) {
        if (options.Child || options.StartupReport == null || startupReported) return;
        string temporary = options.StartupReport + ".tmp";
        File.WriteAllText(temporary, json.Serialize(new { ready = success, process_id = Process.GetCurrentProcess().Id }), Encoding.UTF8);
        File.Move(temporary, options.StartupReport);
        startupReported = true;
    }
    private string MigrationScript()
    {
        var payload = "{}";
        string path = Path.Combine(options.Data, "webview2-migration.json");
        bool hasSnapshot = File.Exists(path);
        if (File.Exists(path)) {
            var bytes = new FileInfo(path).Length;
            if (bytes <= 2 * 1024 * 1024) payload = json.Serialize(json.DeserializeObject(File.ReadAllText(path, Encoding.UTF8)));
        }
        string recovery = "null";
        try {
            if (File.Exists(CloseDraftFile) && new FileInfo(CloseDraftFile).Length <= 8 * 1024 * 1024)
                recovery = json.Serialize(json.DeserializeObject(File.ReadAllText(CloseDraftFile, Encoding.UTF8)));
        } catch (Exception exc) { Log("close-drafts-read: " + exc.GetType().Name); }
        return "(()=>{if(location.origin!==" + json.Serialize(options.Origin.GetLeftPart(UriPartial.Authority)) + "||window.top!==window)return;window.ezreadDesktop=true;try{const mark='ezread-webview2-migrated';if(!localStorage.getItem(mark)&&" + (hasSnapshot ? "true" : "false") + "){const values=" + payload + ";for(const [key,value] of Object.entries(values)){if((key.startsWith('ezread-')||key.startsWith('readx-')||key==='tudu-sort')&&typeof value==='string'&&localStorage.getItem(key)===null)localStorage.setItem(key,value);}localStorage.setItem(mark,'1');}const snapshot=" + recovery + ";if(snapshot?.origin===location.origin&&typeof snapshot.id==='string'&&localStorage.getItem('ezread-close-drafts-id')!==snapshot.id){for(const [key,value] of Object.entries(snapshot.values||{}))if(key.startsWith('ezread-reader-')&&typeof value==='string')localStorage.setItem(key,value);localStorage.setItem('ezread-close-drafts-id',snapshot.id);}}catch{}})();";
    }
    private string CloseDraftFile { get { return Path.Combine(options.Data, "desktop-close-drafts.json"); } }
    private static void ObserveFailure(Task task) {
        task.ContinueWith(failed => { var ignored = failed.Exception; }, TaskContinuationOptions.OnlyOnFaulted);
    }
    private static void DeleteAfterWrite(Task task, string path) {
        task.ContinueWith(delegate { try { File.Delete(path); } catch { } });
    }
    private async Task<T> CloseStep<T>(Task<T> task, int milliseconds, string stage) {
        if (await Task.WhenAny(task, Task.Delay(milliseconds)) != task) {
            // A timed-out WebView operation can finish later. Observe failures;
            // never resume a cancelled native close continuation from it.
            ObserveFailure(task);
            Log("close-timeout stage=" + stage);
            throw new TimeoutException(stage);
        }
        return await task;
    }
    private Task<string> CloseScript(string script, int milliseconds, string stage) {
        return CloseStep(view.CoreWebView2.ExecuteScriptAsync(script), milliseconds, stage);
    }
    private async Task<bool> CaptureCloseDrafts() {
        string raw = await CloseScript("typeof ezreadDesktopDraftSnapshot==='function'?ezreadDesktopDraftSnapshot():null", 2000, "draft-read");
        var values = json.Deserialize<Dictionary<string, string>>(raw);
        if (values == null) return false;
        string id = Guid.NewGuid().ToString();
        var bytes = Encoding.UTF8.GetBytes(json.Serialize(new { id = id, origin = options.Origin.GetLeftPart(UriPartial.Authority), values = values }));
        if (bytes.Length > 8 * 1024 * 1024) return false;
        string temporary = CloseDraftFile + "." + id + ".tmp";
        var writing = Task.Run(delegate {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
            return true;
        });
        try { await CloseStep(writing, 3000, "draft-write"); }
        catch {
            DeleteAfterWrite(writing, temporary);
            throw;
        }
        if (File.Exists(CloseDraftFile)) File.Replace(temporary, CloseDraftFile, null); else File.Move(temporary, CloseDraftFile);
        // A cancelled close may be followed by a page reload. Do not replay
        // this snapshot over newer edits in the still-running browser.
        try { await CloseScript("localStorage.setItem('ezread-close-drafts-id'," + json.Serialize(id) + ")", 1000, "draft-marker"); }
        catch (Exception exc) { Log("close-drafts-marker: " + exc.GetType().Name); }
        return true;
    }
    private void ClearCloseDrafts() {
        try { if (!options.Child && File.Exists(CloseDraftFile)) File.Delete(CloseDraftFile); }
        catch (Exception exc) { Log("close-drafts-clear: " + exc.GetType().Name); }
    }
    private void ShowWarning(string message, MessageBoxIcon icon, bool closeAfter = false) {
        // WebView2 disallows nested modal loops inside its callbacks, including
        // ExecuteScriptAsync continuations. Post after the callback returns.
        if (ClosingWindow || !IsHandleCreated) return;
        BeginInvoke((Action)delegate {
            if (ClosingWindow) return;
            MessageBox.Show(this, message, "EzRead", MessageBoxButtons.OK, icon);
            if (closeAfter && !IsDisposed) Close();
        });
    }
    private void OpenExternal(string url) {
        Uri target;
        if (!Uri.TryCreate(url, UriKind.Absolute, out target) || (target.Scheme != "http" && target.Scheme != "https") || !String.IsNullOrEmpty(target.UserInfo)) return;
        try { Process.Start(new ProcessStartInfo(target.AbsoluteUri) { UseShellExecute = true }); }
        catch (Exception exc) { Log("external-link: " + exc.GetType().Name); }
    }
    private void RemoveOwnTaskbarButton() {
        if (options.Child || !IsHandleCreated || IsDisposed) return;
        try { Log("taskbar-remove hwnd=" + Handle.ToInt64() + " hr=" + NativeTaskbar.SetVisible(Handle, false)); }
        catch (Exception exc) { Log("taskbar-remove: " + exc.GetType().Name); }
    }
    private async void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (mayClose) return;
        // The main window has already saved before closing its PDF children.
        var owner = Owner as ReaderWindow;
        if (options.Child && e.CloseReason == CloseReason.FormOwnerClosing && owner != null && owner.mayClose) { mayClose = true; return; }
        e.Cancel = true;
        if (preparingClose) return;
        preparingClose = true;
        bool localDraftsSaved = false;
        Log("close-request");
        var visibleWindows = new List<Form>();
        if (Visible) visibleWindows.Add(this);
        foreach (var child in OwnedForms) if (child.Visible) visibleWindows.Add(child);
        try {
            Exception closeError = null;
            try {
                SaveBounds();
                // Keep the WebView alive for its save acknowledgement, but remove
                // native windows immediately. Backend cleanup finishes while hidden.
                Hide();
                RemoveOwnTaskbarButton();
                viewportTimer.Stop();
                foreach (var child in visibleWindows) if (child != this) child.Hide();
                // ExecuteScriptAsync does not await JavaScript Promises. Use a small
                // explicit acknowledgement and poll it while the UI stays responsive.
                if (ready) {
                    // Capture recoverable edits before awaiting network saves. If the
                    // renderer dies afterwards, closing must not resurrect a dead view.
                    if (!options.Child) localDraftsSaved = await CaptureCloseDrafts();
                    Log("close-local-drafts=" + localDraftsSaved);
                    string request = json.Serialize(Guid.NewGuid().ToString());
                    var saving = Stopwatch.StartNew();
                    await CloseScript("window.__ezreadCloseResult=null;window.__ezreadCloseRequest=" + request + ";(async()=>{let result=false;try{result=typeof ezreadPrepareDesktopClose==='function'?await ezreadPrepareDesktopClose():true;}catch{}if(window.__ezreadCloseRequest===" + request + ")window.__ezreadCloseResult=result;})();", 2000, "save-start");
                    string answer = "null";
                    while (answer == "null" && saving.ElapsedMilliseconds < 8000) {
                        await Task.Delay(100);
                        int remaining = Math.Max(1, 8000 - (int)saving.ElapsedMilliseconds);
                        answer = await CloseScript("window.__ezreadCloseResult", Math.Min(2000, remaining), "save-confirmation");
                    }
                    if (answer != "true") {
                        if (webviewFailed) throw new InvalidOperationException("WebView2 exited during close saving.");
                        if (answer == "null") {
                            Log("close-timeout stage=save-confirmation");
                            throw new TimeoutException("save-confirmation");
                        }
                        Log("close-save-unconfirmed result=" + answer);
                        RestoreClosingWindows(visibleWindows);
                        ShowWarning("仍有内容未能保存，窗口暂未关闭。请确认草稿已保存后重试。", MessageBoxIcon.Warning);
                        return;
                    }
                    Log("close-save-confirmed");
                    ClearCloseDrafts();
                }
                if (backend != null) await Task.Run((Action)backend.Stop);
                mayClose = true; Close();
            } catch (Exception exc) { closeError = exc; }
            if (closeError == null) return;
            Log("close-save: " + closeError.GetType().Name);
            if (webviewFailed && localDraftsSaved) {
                Log("close-with-local-drafts-after-webview-failure");
                if (backend != null) { try { await Task.Run((Action)backend.Stop); } catch (Exception stop) { Log("backend-close: " + stop.GetType().Name); } }
                mayClose = true; Close();
            } else {
                mayClose = false;
                if (webviewFailed) {
                    try {
                        viewport.Controls.Remove(view); view.Dispose();
                        view = new WebView2 { Location = Point.Empty, DefaultBackgroundColor = BackColor, Size = viewport.ClientSize };
                        viewport.Controls.Add(view);
                        await InitializeWebView(true);
                        Log("close-recovery-recreated-webview");
                    } catch (Exception recovery) { Log("close-recovery: " + recovery.GetType().Name); }
                }
                RestoreClosingWindows(visibleWindows);
                string reason = closeError is TimeoutException
                    ? "关闭等待超时，窗口已恢复，尚未确认保存的内容不会被强制丢弃。请稍后重试。"
                    : "无法完成关闭，窗口已恢复，请稍后重试。";
                ShowWarning(reason, MessageBoxIcon.Warning);
            }
        }
        finally { preparingClose = false; }
    }
    private void RestoreClosingWindows(List<Form> windows) {
        Log("close-restored");
        foreach (var window in windows) if (!window.IsDisposed && !window.Disposing) window.Show();
        if (!options.Child && ShowInTaskbar && IsHandleCreated) {
            try { Log("taskbar-restore hwnd=" + Handle.ToInt64() + " hr=" + NativeTaskbar.SetVisible(Handle, true)); }
            catch (Exception exc) { Log("taskbar-restore: " + exc.GetType().Name); }
        }
        preparingClose = false;
        RequestViewportFit();
        if (Visible && options.SmokeReport == null) Activate();
    }
    private void RestoreBoundsFromDisk() {
        if (options.Child) return;
        try {
            string file = Path.Combine(options.Data, "desktop-window.json");
            if (!File.Exists(file)) return;
            var data = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(file));
            var bounds = new Rectangle(Convert.ToInt32(data["left"]), Convert.ToInt32(data["top"]), Convert.ToInt32(data["width"]), Convert.ToInt32(data["height"]));
            if (bounds.Width < 64 || bounds.Height < 64 || bounds.Width > 10000 || bounds.Height > 10000) return;
            foreach (var screen in Screen.AllScreens) if (Rectangle.Intersect(screen.WorkingArea, bounds).Width >= 100 && Rectangle.Intersect(screen.WorkingArea, bounds).Height >= 80) { StartPosition = FormStartPosition.Manual; Bounds = bounds; break; }
            if (data.ContainsKey("maximized") && Convert.ToBoolean(data["maximized"])) WindowState = FormWindowState.Maximized;
        } catch { }
    }
    private void SaveBounds() {
        if (options.Child) return;
        try {
            var bounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
            string file = Path.Combine(options.Data, "desktop-window.json");
            File.WriteAllText(file + ".tmp", json.Serialize(new { left = bounds.Left, top = bounds.Top, width = bounds.Width, height = bounds.Height, maximized = WindowState == FormWindowState.Maximized }), Encoding.UTF8);
            if (File.Exists(file)) File.Replace(file + ".tmp", file, null); else File.Move(file + ".tmp", file);
        } catch (Exception exc) { Log("window-position: " + exc.GetType().Name); }
    }
    private async Task SetSmokeViewport(Size monitor, Size client) {
        smokeMonitorSize = monitor; UpdateMinimumSize(MonitorArea());
        var frame = WindowFrameSize();
        SetViewportOffset(0); Size = new Size(client.Width + frame.Width, client.Height + frame.Height);
        RequestViewportFit(); await Task.Delay(350); await FitViewport(); await Task.Delay(200);
    }
    private async Task WaitSmokeLayout() {
        // Transparent/offscreen WebViews throttle animation frames. Let the
        // real resize observer and its scheduled masonry pass finish first.
        await view.CoreWebView2.ExecuteScriptAsync("window.__smokeLayoutReady=false;requestAnimationFrame(()=>requestAnimationFrame(()=>window.__smokeLayoutReady=true));");
        for (int attempt = 0; attempt < 80; attempt++) {
            await Task.Delay(50);
            if (await view.CoreWebView2.ExecuteScriptAsync("window.__smokeLayoutReady") == "true") return;
        }
        throw new InvalidOperationException("The transparent DPI fixture did not finish its layout frames.");
    }
    private async Task SmokeWheel(bool sidebar, double delta) {
        string value = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify({x:" + (sidebar ? "20" : "document.querySelector('.sidebar').getBoundingClientRect().right+80") + ",y:" + viewportOffset + "/devicePixelRatio+30})");
        var point = json.Deserialize<Dictionary<string, object>>(json.Deserialize<string>(value));
        await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent", json.Serialize(new { type = "mouseWheel", x = Convert.ToDouble(point["x"]), y = Convert.ToDouble(point["y"]), deltaX = 0, deltaY = delta }));
        await Task.Delay(200);
    }
    private async Task<object> SmokeWheelState(string label) {
        string value = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify({cardScroll:scrollY,sidebarScroll:document.querySelector('.sidebar').scrollTop})");
        return new { label = label, offset = viewportOffset, maximum = Math.Max(0, view.Height - ViewportClientSize().Height), viewTop = view.Top,
            horizontal = viewport.HorizontalScroll.Visible, vertical = viewport.VerticalScroll.Visible, page = json.DeserializeObject(json.Deserialize<string>(value)) };
    }
    private async Task<object> SmokeLayout(string label) {
        string cardBox = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify((()=>{const n=document.querySelector('.paper-card'),s=getComputedStyle(n),covers=[...document.querySelectorAll('.card-cover img')];return{borderTop:parseFloat(s.borderTopWidth),borderBottom:parseFloat(s.borderBottomWidth),paddingTop:parseFloat(s.paddingTop),paddingBottom:parseFloat(s.paddingBottom),height:n.getBoundingClientRect().height,bodyHeight:n.querySelector('.card-body').getBoundingClientRect().height,contentHeight:n.querySelector('.card-content').getBoundingClientRect().height,pixelRatio:devicePixelRatio,coverCount:covers.length,loadedCovers:covers.filter(n=>n.complete&&n.naturalWidth>0).length,coverRatios:[...new Set(covers.map(n=>n.naturalWidth+'/'+n.naturalHeight))]};})())");
        string layout = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify({viewport:document.body.clientWidth,height:innerHeight,innerWidth:innerWidth,documentWidth:document.documentElement.scrollWidth,overflowX:getComputedStyle(document.documentElement).overflowX,tracks:getComputedStyle(document.querySelector('#paper-grid')).gridTemplateColumns.split(/\\s+/).map(parseFloat),grid:document.querySelector('#paper-grid').getBoundingClientRect().toJSON(),container:document.querySelector('#library-view').getBoundingClientRect().toJSON(),sidebar:document.querySelector('.sidebar').getBoundingClientRect().width,footer:document.querySelector('.library-footer').getBoundingClientRect().toJSON(),dialog:[...document.querySelectorAll('dialog[open]')].map(n=>({id:n.id,...n.getBoundingClientRect().toJSON()})),cards:[...document.querySelectorAll('.paper-card')].map(n=>({id:n.dataset.paperId,x:n.offsetLeft,y:n.offsetTop,width:n.offsetWidth,height:n.offsetHeight,title:n.querySelector('.card-title').textContent}))})");
        using (var preview = new MemoryStream()) {
            await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, preview);
            preview.Position = 0;
            using (var bitmap = new Bitmap(preview)) {
                int top = viewportOffset;
                var crop = new Rectangle(0, top, Math.Min(viewport.ClientSize.Width, bitmap.Width), Math.Min(viewport.ClientSize.Height, bitmap.Height - top));
                using (var visible = bitmap.Clone(crop, bitmap.PixelFormat)) visible.Save(options.SmokeReport + "." + label + ".png", System.Drawing.Imaging.ImageFormat.Png);
            }
        }
        var physicalClient = ViewportClientSize();
        return new { label = label, clientWidth = physicalClient.Width, clientHeight = physicalClient.Height,
            windowWidth = Width, windowClientWidth = ClientSize.Width, windowState = WindowState.ToString(),
            windowDpi = GetDpiForWindow(Handle), scrollbarWidth = WindowScrollBarWidth(), systemScrollbarWidth = SystemInformation.VerticalScrollBarWidth,
            monitorWidth = MonitorArea().Width, monitorHeight = MonitorArea().Height,
            viewportWidth = viewport.Width, viewportHeight = viewport.Height,
            horizontalScroll = viewport.HorizontalScroll.Visible, verticalScroll = viewport.VerticalScroll.Visible,
            viewWidth = view.Width, viewHeight = view.Height, minimumWidth = MinimumSize.Width, minimumHeight = MinimumSize.Height,
            scrollY = viewportOffset, maximumOffset = Math.Max(0, view.Height - physicalClient.Height), viewTop = view.Top,
            zoomFactor = view.ZoomFactor, cardBox = json.DeserializeObject(json.Deserialize<string>(cardBox)), layout = json.DeserializeObject(json.Deserialize<string>(layout)) };
    }
    private async Task SmokeTest(CoreWebView2Environment environment) {
        await Task.Delay(1600);
        // Fixture covers must finish loading before comparing geometry. Lazy
        // loading must not turn a cross-monitor test into a loading-time test.
        await view.CoreWebView2.ExecuteScriptAsync("window.__smokeCoversReady=false;(async()=>{const covers=[...document.querySelectorAll('.card-cover img')];covers.forEach(n=>n.loading='eager');await Promise.allSettled(covers.map(n=>n.decode()));window.__smokeCoversReady=true;})();");
        for (int i = 0; i < 100; i++) { if (await view.CoreWebView2.ExecuteScriptAsync("window.__smokeCoversReady") == "true") break; await Task.Delay(50); }
        var frame = WindowFrameSize();
        var layouts = new List<object>();
        foreach (var size in new[] { new Size(1366, 768), new Size(1920, 1080), new Size(2560, 1440) }) {
            await SetSmokeViewport(size, new Size(size.Width - frame.Width, size.Height - frame.Height));
            layouts.Add(await SmokeLayout(size.Width.ToString()));
        }
        var viewportChecks = new List<object>();
        var wheelChecks = new List<object>();
        // Full-height snaps and restoration must not gain native scrollbars.
        foreach (var monitor in new[] { new Size(1366, 768), new Size(1920, 1080), new Size(2560, 1440), new Size(1280, 1440), new Size(1080, 1920) }) {
            string label = "snap-" + monitor.Width + "x" + monitor.Height;
            await SetSmokeViewport(monitor, new Size(monitor.Width - frame.Width, monitor.Height - frame.Height));
            viewportChecks.Add(await SmokeLayout(label + "-full"));
            await SetSmokeViewport(monitor, new Size((monitor.Width + 1) / 2 - frame.Width, monitor.Height - frame.Height));
            viewportChecks.Add(await SmokeLayout(label + "-half"));
            await SetSmokeViewport(monitor, new Size(monitor.Width - frame.Width, monitor.Height - frame.Height));
            viewportChecks.Add(await SmokeLayout(label + "-restored"));
        }
        var landscape = new Size(1920, 1080);
        int landscapeHeight = landscape.Height - frame.Height;
        await SetSmokeViewport(landscape, new Size(landscape.Width - frame.Width, landscapeHeight)); viewportChecks.Add(await SmokeLayout("normal-five"));
        foreach (var width in new[] { 1600, 1280, 1000 }) {
            await SetSmokeViewport(landscape, new Size(width, landscapeHeight)); viewportChecks.Add(await SmokeLayout("normal-" + width));
        }
        await SetSmokeViewport(landscape, new Size(1280, 540)); viewportChecks.Add(await SmokeLayout("flat"));
        wheelChecks.Add(await SmokeWheelState("before"));
        await SmokeWheel(true, 150); wheelChecks.Add(await SmokeWheelState("sidebar-down"));
        await SmokeWheel(false, 150); wheelChecks.Add(await SmokeWheelState("cards-down"));
        await SmokeWheel(true, -150); wheelChecks.Add(await SmokeWheelState("sidebar-up"));
        await SmokeWheel(true, 10000); wheelChecks.Add(await SmokeWheelState("sidebar-bottom"));
        await SmokeWheel(true, 10000); wheelChecks.Add(await SmokeWheelState("sidebar-bottom-again"));
        await SmokeWheel(true, -10000); wheelChecks.Add(await SmokeWheelState("sidebar-top"));
        await view.CoreWebView2.ExecuteScriptAsync("scrollTo(0,0)"); await Task.Delay(200);
        SetViewportOffset(10000); await Task.Delay(150); viewportChecks.Add(await SmokeLayout("flat-scrolled"));
        var portrait = new Size(1080, 1920);
        await SetSmokeViewport(portrait, new Size(portrait.Width - frame.Width, portrait.Height - frame.Height)); viewportChecks.Add(await SmokeLayout("portrait"));
        await SetSmokeViewport(portrait, new Size(1, 1)); viewportChecks.Add(await SmokeLayout("portrait-minimum"));
        SetViewportOffset(10000); int offsetBeforeModal = viewportOffset;
        await view.CoreWebView2.ExecuteScriptAsync("openSettings()"); await Task.Delay(350); await FitViewport(); await WaitSmokeLayout(); viewportChecks.Add(await SmokeLayout("short-settings"));
        await SmokeWheel(true, 150); viewportChecks.Add(await SmokeLayout("short-settings-wheel"));
        await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#settings-dialog').close()");
        await Task.Delay(350); await FitViewport();
        wheelChecks.Add(new { label = "modal-restored", before = offsetBeforeModal, offset = viewportOffset });
        // Exercise actual monitor DPI, including non-client scrollbars. Opacity
        // keeps our fixture window invisible while Windows delivers DPI changes.
        var dpiChecks = new List<object>(); var originalLocation = Location;
        Opacity = 0;
        int screenIndex = 0;
        foreach (var screen in Screen.AllScreens) {
            Location = new Point(screen.WorkingArea.Left + 40, screen.WorkingArea.Top + 40);
            await Task.Delay(200);
            var monitor = screen.WorkingArea.Size; var monitorFrame = WindowFrameSize();
            string label = "dpi-screen-" + screenIndex++;
            await SetSmokeViewport(monitor, new Size(monitor.Width - monitorFrame.Width, monitor.Height - monitorFrame.Height));
            await WaitSmokeLayout();
            dpiChecks.Add(await SmokeLayout(label + "-full"));
            await SetSmokeViewport(monitor, new Size((int)(monitor.Width * .66) - monitorFrame.Width, (int)(monitor.Height * .75) - monitorFrame.Height));
            await WaitSmokeLayout();
            dpiChecks.Add(await SmokeLayout(label + "-short"));
            wheelChecks.Add(await SmokeWheelState(label + "-wheel-before"));
            await SmokeWheel(true, 150); wheelChecks.Add(await SmokeWheelState(label + "-wheel-down"));
            await SmokeWheel(true, -150); wheelChecks.Add(await SmokeWheelState(label + "-wheel-up"));
            await SetSmokeViewport(monitor, new Size((monitor.Width + 1) / 2 - monitorFrame.Width, monitor.Height - monitorFrame.Height));
            await WaitSmokeLayout();
            dpiChecks.Add(await SmokeLayout(label + "-half"));
        }
        Location = originalLocation; await Task.Delay(200); Opacity = 1;
        await SetSmokeViewport(new Size(2560, 1440), new Size(2560 - frame.Width, 1440 - frame.Height));
        double beforeSettingsZoom = view.ZoomFactor;
        await view.CoreWebView2.ExecuteScriptAsync("window.__settingsProbe=null;(async()=>{const snap=()=>({width:document.body.clientWidth,scroll:scrollY,cards:[...document.querySelectorAll('.paper-card')].map(n=>[n.offsetLeft,n.offsetTop,n.offsetWidth,n.offsetHeight])});scrollTo(0,180);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const before=snap();await openSettings();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const frames=[];document.querySelector('#settings-tab-journals').click();for(let i=0;i<45;i++){await new Promise(requestAnimationFrame);frames.push(snap());}document.querySelector('#settings-tab-appearance').click();for(let i=0;i<30;i++){await new Promise(requestAnimationFrame);frames.push(snap());}document.querySelector('#settings-dialog').close();for(let i=0;i<15;i++){await new Promise(requestAnimationFrame);frames.push(snap());}window.__settingsProbe={before,frames};})();");
        string settingsProbe = "null";
        for (int i = 0; i < 80 && settingsProbe == "null"; i++) { await Task.Delay(50); settingsProbe = await view.CoreWebView2.ExecuteScriptAsync("window.__settingsProbe ? JSON.stringify(window.__settingsProbe) : null"); }
        var settingsBackground = new { beforeZoom = beforeSettingsZoom, afterZoom = view.ZoomFactor, before = settingsProbe == "null" ? null : ((Dictionary<string, object>)json.DeserializeObject(json.Deserialize<string>(settingsProbe)))["before"], frames = settingsProbe == "null" ? null : ((Dictionary<string, object>)json.DeserializeObject(json.Deserialize<string>(settingsProbe)))["frames"] };
        string state = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify({title:document.title,cards:document.querySelectorAll('.paper-card').length,desktop:window.ezreadDesktop===true,closeHook:typeof ezreadPrepareDesktopClose==='function',stored:localStorage.getItem('ezread-sort'),body:!!document.querySelector('.app-shell'),draftPresent:localStorage.getItem('ezread-reader-notes:smoke-paper')!==null,errors:window.__ezreadSmokeErrors})");
        IntPtr big = SendMessage(Handle, 0x7f, new IntPtr(1), IntPtr.Zero);
        bool iconMatches = false;
        IntPtr expectedHandle = LoadImage(IntPtr.Zero, Path.Combine(options.Root, "static", "ezread.ico"), 1, 32, 32, 0x10);
        try {
            if (big != IntPtr.Zero && expectedHandle != IntPtr.Zero) using (var actual = Icon.FromHandle(big).ToBitmap()) using (var expected = Icon.FromHandle(expectedHandle).ToBitmap()) {
                iconMatches = actual.Size == expected.Size;
                for (int y = 0; iconMatches && y < actual.Height; y++) for (int x = 0; x < actual.Width; x++) if (actual.GetPixel(x, y) != expected.GetPixel(x, y)) { iconMatches = false; break; }
            }
        } finally { if (expectedHandle != IntPtr.Zero) DestroyIcon(expectedHandle); }
        var report = new { processId = Process.GetCurrentProcess().Id, browserProcessId = view.CoreWebView2.BrowserProcessId, webViewVersion = environment.BrowserVersionString, hostExecutable = Application.ExecutablePath, windowTitle = Text, bigIconPresent = big != IntPtr.Zero, bigIconOwned = big == Icon.Handle, bigIconMatchesBook = iconMatches, captionIconOwned = SendMessage(Handle, 0x7f, IntPtr.Zero, IntPtr.Zero) == captionIcon.Handle, page = json.DeserializeObject(json.Deserialize<string>(state)), layouts = layouts, viewportChecks = viewportChecks, dpiChecks = dpiChecks, wheelChecks = wheelChecks, settingsBackground = settingsBackground };
        File.WriteAllText(options.SmokeReport, json.Serialize(report), Encoding.UTF8);
        Close();
    }
    private void Log(string message) { try { Directory.CreateDirectory(options.Data); File.AppendAllText(Path.Combine(options.Data, "desktop.log"), DateTime.Now.ToString("s") + " pid=" + Process.GetCurrentProcess().Id + " child=" + options.Child + " " + message + Environment.NewLine, Encoding.UTF8); } catch { } }
    protected override void Dispose(bool disposing) {
        if (disposing) { viewportTimer.Dispose(); view.Dispose(); if (Icon != null) Icon.Dispose(); if (captionIcon != null) captionIcon.Dispose(); }
        base.Dispose(disposing);
    }
}
