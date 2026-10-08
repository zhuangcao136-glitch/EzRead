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
    public string Root, Data, Url, SmokeReport;
    public bool Child;
    public Uri Origin;
    public static ShellOptions Parse(string[] args)
    {
        var values = new Dictionary<string, string>();
        for (int i = 0; i < args.Length; i += 2) {
            if (i + 1 >= args.Length || (args[i] != "--app-root" && args[i] != "--data-dir" && args[i] != "--url" && args[i] != "--smoke-test"))
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
            SetCurrentProcessExplicitAppUserModelID("EzRead.Desktop");
            string identity;
            using (var hash = SHA256.Create()) identity = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(options.Root.ToUpperInvariant() + "|" + options.Data.ToUpperInvariant() + "|" + options.Origin.GetLeftPart(UriPartial.Authority)))).Replace("-", "").Substring(0, 24);
            using (var mutex = new Mutex(false, "Local\\EzRead.Desktop." + identity))
            using (var wake = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\EzRead.Desktop.Wake." + identity)) {
                bool owned;
                try { owned = mutex.WaitOne(0); } catch (AbandonedMutexException) { owned = true; }
                if (!owned) { wake.Set(); return 0; }
                NativeProcessLifetime.Attach();
                try {
                    using (var form = new ReaderWindow(options)) {
                        ThreadPool.QueueUserWorkItem(delegate {
                            try {
                                while (!form.IsDisposed) {
                                    if (!wake.WaitOne(500)) continue;
                                    if (form.IsHandleCreated && !form.IsDisposed) form.BeginInvoke((Action)delegate {
                                        form.ActivateExisting();
                                    });
                                }
                            } catch (ObjectDisposedException) { } catch (InvalidOperationException) { }
                        });
                        Application.Run(form);
                        return form.ExitCode;
                    }
                } finally { mutex.ReleaseMutex(); }
            }
        } catch (Exception exc) {
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

internal sealed class OwnedBackend : IDisposable
{
    private readonly ShellOptions options;
    private readonly JavaScriptSerializer json = new JavaScriptSerializer();
    private Process process;
    private string instance;
    private readonly object gate = new object();
    public OwnedBackend(ShellOptions value) { options = value; }
    private Dictionary<string, object> Request(string path, string body = null) {
        var request = (HttpWebRequest)WebRequest.Create(new Uri(options.Origin, path));
        request.Proxy = null; request.Timeout = 2000; request.ReadWriteTimeout = 2000;
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
        Request("/api/desktop-attach", json.Serialize(new { instance_id = instance, process_id = Process.GetCurrentProcess().Id }));
    }
    public void Stop() {
        lock (gate) {
        if (process != null && process.HasExited) return;
        // Recapture a backend deliberately reloaded while this window was open.
        try { CaptureCore(); } catch { if (process == null || process.HasExited) return; }
        try { Request("/api/shutdown", json.Serialize(new { instance_id = instance })); } catch (WebException) { }
        if (!process.WaitForExit(10000)) { process.Kill(); process.WaitForExit(3000); }
        }
    }
    public void Dispose() { lock (gate) { if (process != null) process.Dispose(); } }
}

internal sealed class ReaderWindow : Form
{
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern IntPtr LoadImage(IntPtr instance, string name, uint type, int width, int height, uint flags);
    [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
    private readonly ShellOptions options;
    private readonly WebView2 view = new WebView2();
    private readonly Panel viewport = new Panel { AutoScroll = true, Dock = DockStyle.Fill };
    private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
    private readonly Icon captionIcon;
    private readonly System.Windows.Forms.Timer viewportTimer = new System.Windows.Forms.Timer { Interval = 100 };
    private const double LayoutWidth = 1920;
    private Size? smokeMonitorSize;
    private Rectangle fittedMonitorArea;
    private double fittedDeviceScale;
    private bool fittingViewport;
    private bool ready, preparingClose, mayClose;
    private readonly OwnedBackend backend;
    public int ExitCode;
    public ReaderWindow(ShellOptions value)
    {
        options = value;
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
        if (options.SmokeReport != null) { ShowInTaskbar = false; StartPosition = FormStartPosition.Manual; Location = new Point(-20000, -20000); }
        Shown += async delegate { await InitializeWebView(); };
        FormClosing += OnClosing;
        FormClosed += delegate { if (backend != null) { try { backend.Stop(); } catch (Exception exc) { Log("backend-close: " + exc.GetType().Name); } finally { backend.Dispose(); } } };
    }
    protected override void WndProc(ref Message message)
    {
        // Only our own caption icon is transparent. The real large icon belongs
        // to this executable/Form and lives for the entire window lifetime.
        if (captionIcon != null && message.Msg == 0x80 && message.WParam == IntPtr.Zero) message.LParam = captionIcon.Handle;
        if (message.Msg == 0x80 && message.WParam == new IntPtr(1) && Icon != null) message.LParam = Icon.Handle;
        if (captionIcon != null && message.Msg == 0x7f && (message.WParam == IntPtr.Zero || message.WParam == new IntPtr(2))) { message.Result = captionIcon.Handle; return; }
        base.WndProc(ref message);
    }
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        if (captionIcon != null) SendMessage(Handle, 0x80, IntPtr.Zero, captionIcon.Handle);
        if (Icon != null) SendMessage(Handle, 0x80, new IntPtr(1), Icon.Handle);
    }
    protected override bool ShowWithoutActivation { get { return options.SmokeReport != null; } }
    public void ActivateExisting() {
        if (preparingClose || mayClose || IsDisposed || Disposing) return;
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        Show(); if (options.SmokeReport == null) Activate();
    }
    private void RequestViewportFit() {
        if (!ready || IsDisposed) return;
        viewportTimer.Stop(); viewportTimer.Start();
    }
    private Rectangle MonitorArea() {
        return smokeMonitorSize.HasValue ? new Rectangle(Point.Empty, smokeMonitorSize.Value) : Screen.FromControl(this).WorkingArea;
    }
    private void UpdateMinimumSize(Rectangle area) {
        var minimum = new Size((area.Width + 1) / 2, (area.Height + 1) / 2);
        if (MinimumSize != minimum) MinimumSize = minimum;
    }
    private async Task FitViewport() {
        if (!ready || IsDisposed || WindowState == FormWindowState.Minimized) return;
        if (fittingViewport) { RequestViewportFit(); return; }
        fittingViewport = true;
        try {
            var area = MonitorArea(); UpdateMinimumSize(area);
            var frame = SizeFromClientSize(Size.Empty);
            // Reserve the native vertical scrollbar's lane even while hidden;
            // showing it after a height-only resize must not move the cards.
            int fullWidth = Math.Max(1, area.Width - frame.Width - SystemInformation.VerticalScrollBarWidth), fullHeight = Math.Max(1, area.Height - frame.Height);
            if (options.Child) {
                view.ZoomFactor = 1;
                view.Size = new Size(Math.Max(1, viewport.Width - SystemInformation.VerticalScrollBarWidth), fullHeight);
                return;
            }
            string value = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify(ezreadDesktopViewportMetrics())");
            var metrics = json.Deserialize<Dictionary<string, object>>(json.Deserialize<string>(value));
            double gutter = Convert.ToDouble(metrics["gutter"]);
            double deviceScale = Convert.ToDouble(metrics["pixelRatio"]) / view.ZoomFactor;
            // Use the monitor's longer axis, so portrait screens keep readable
            // cards and fewer columns. Window resize never determines page zoom.
            if (area != fittedMonitorArea || Math.Abs(deviceScale - fittedDeviceScale) > .0001) {
                // Calibrate once against the monitor, then lock this zoom for
                // normal resizes. Temporary reference width also works on a
                // portrait monitor and when startup restores a small window.
                view.Size = new Size(Math.Max(fullWidth, fullHeight), fullHeight);
                await Task.Delay(75);
                for (int attempt = 0; attempt < 4; attempt++) {
                    value = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify(ezreadDesktopViewportMetrics())");
                    metrics = json.Deserialize<Dictionary<string, object>>(json.Deserialize<string>(value));
                    gutter = Convert.ToDouble(metrics["gutter"]);
                    double target = Math.Max(.25, Math.Min(5, Math.Max(fullWidth, fullHeight) / deviceScale / (LayoutWidth + gutter)));
                    if (Math.Abs(target - view.ZoomFactor) <= .0001) break;
                    view.ZoomFactor = target;
                    await Task.Delay(75);
                }
                fittedMonitorArea = area; fittedDeviceScale = deviceScale;
            }
            double zoom = view.ZoomFactor;
            int minimumWidth = (int)Math.Ceiling((Convert.ToDouble(metrics["minimumWidth"]) + gutter) * deviceScale * zoom);
            var size = new Size(Math.Max(viewport.Width - SystemInformation.VerticalScrollBarWidth, minimumWidth), fullHeight);
            if (view.Size != size) view.Size = size;
            // Native scrollbars pan the entire view, even while a modal dialog
            // makes its document background inert. Keep positions on resize.
        } catch (Exception exc) { Log("viewport-fit: " + exc.GetType().Name); }
        finally { fittingViewport = false; }
    }
    private async Task InitializeWebView()
    {
        try {
            Log("initializing");
            if (backend != null) {
                try { await Task.Run((Action)backend.Capture); }
                catch { if (options.SmokeReport == null) throw; }
            }
            CoreWebView2Environment.GetAvailableBrowserVersionString();
            Log("runtime-detected");
            var environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(options.Data, "webview2-profile"));
            Log("environment-created");
            await view.EnsureCoreWebView2Async(environment);
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
            if (options.SmokeReport != null) await core.AddScriptToExecuteOnDocumentCreatedAsync("(()=>{window.__ezreadSmokeErrors=[];window.addEventListener('error',e=>window.__ezreadSmokeErrors.push(e.message));window.addEventListener('unhandledrejection',e=>window.__ezreadSmokeErrors.push(String(e.reason)));document.addEventListener('DOMContentLoaded',()=>new MutationObserver(changes=>{for(const change of changes)for(const node of change.addedNodes)if(node.classList?.contains('error'))window.__ezreadSmokeErrors.push(node.textContent);}).observe(document.querySelector('#toasts'),{childList:true}),{once:true});})();");
            if (!options.Child) {
                core.WebMessageReceived += delegate(object sender, CoreWebView2WebMessageReceivedEventArgs e) {
                    if (!options.IsLocal(e.Source)) return;
                    Uri source; if (!Uri.TryCreate(e.Source, UriKind.Absolute, out source) || (source.AbsolutePath != "/" && source.AbsolutePath != "/static/index.html")) return;
                    try { if (e.TryGetWebMessageAsString() == "ezread-fit-viewport") RequestViewportFit(); } catch (ArgumentException) { }
                };
                await core.AddScriptToExecuteOnDocumentCreatedAsync("(()=>{if(window.top!==window)return;const fit=()=>window.chrome?.webview?.postMessage('ezread-fit-viewport');window.addEventListener('resize',fit);document.addEventListener('DOMContentLoaded',()=>{let font=getComputedStyle(document.documentElement).fontSize;new MutationObserver(()=>{const next=getComputedStyle(document.documentElement).fontSize;if(next!==font){font=next;fit();}}).observe(document.documentElement,{attributes:true,attributeFilter:['style']});fit();},{once:true});})();");
                view.ZoomFactorChanged += delegate { RequestViewportFit(); };
            }
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) {
                if (options.IsLocal(e.Uri) || e.Uri == "about:blank") return;
                e.Cancel = true;
                if (e.IsUserInitiated) OpenExternal(e.Uri);
            };
            core.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) {
                e.Handled = true;
                if (!e.IsUserInitiated) return;
                if (!options.IsLocal(e.Uri)) { OpenExternal(e.Uri); return; }
                var childOptions = new ShellOptions { Root = options.Root, Data = options.Data, Origin = options.Origin, Url = e.Uri, Child = true };
                var child = new ReaderWindow(childOptions);
                child.Show(this);
            };
            core.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs e) {
                e.Handled = true;
                using (var dialog = new SaveFileDialog()) {
                    dialog.FileName = Path.GetFileName(e.ResultFilePath);
                    dialog.OverwritePrompt = true; dialog.RestoreDirectory = true;
                    if (dialog.ShowDialog(this) != DialogResult.OK) { e.Cancel = true; return; }
                    e.ResultFilePath = dialog.FileName;
                }
            };
            core.ProcessFailed += delegate { ready = false; Log("webview-process-failed"); MessageBox.Show(this, "阅读窗口遇到问题，请关闭后重新打开。已保存的论文和草稿会保留。", "EzRead", MessageBoxButtons.OK, MessageBoxIcon.Warning); };
            core.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs e) {
                ready = e.IsSuccess;
                if (!e.IsSuccess) { Log("navigation-failed: " + e.WebErrorStatus); return; }
                Log("ready WebView2=" + environment.BrowserVersionString);
                await FitViewport();
                if (options.SmokeReport != null) await SmokeTest(environment);
            };
            core.Navigate(options.Url);
        } catch (Exception exc) {
            ExitCode = 1; Log(exc.ToString());
            if (options.SmokeReport != null) {
                File.WriteAllText(options.SmokeReport, json.Serialize(new { error = exc.ToString() }), Encoding.UTF8);
                mayClose = true; Close(); return;
            }
            MessageBox.Show(this, "桌面窗口初始化失败：\n" + exc.Message + "\n如提示缺少运行时，请安装 Microsoft Edge WebView2 Runtime。", "EzRead", MessageBoxButtons.OK, MessageBoxIcon.Error);
            mayClose = true; Close();
        }
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
        return "(()=>{if(location.origin!==" + json.Serialize(options.Origin.GetLeftPart(UriPartial.Authority)) + "||window.top!==window)return;window.ezreadDesktop=true;try{const mark='ezread-webview2-migrated';if(localStorage.getItem(mark)||" + (hasSnapshot ? "false" : "true") + ")return;const values=" + payload + ";for(const [key,value] of Object.entries(values)){if((key.startsWith('ezread-')||key.startsWith('readx-')||key==='tudu-sort')&&typeof value==='string'&&localStorage.getItem(key)===null)localStorage.setItem(key,value);}localStorage.setItem(mark,'1');}catch{}})();";
    }
    private void OpenExternal(string url) {
        Uri target;
        if (!Uri.TryCreate(url, UriKind.Absolute, out target) || (target.Scheme != "http" && target.Scheme != "https") || !String.IsNullOrEmpty(target.UserInfo)) return;
        try { Process.Start(new ProcessStartInfo(target.AbsoluteUri) { UseShellExecute = true }); }
        catch (Exception exc) { Log("external-link: " + exc.GetType().Name); }
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
        var visibleWindows = new List<Form>();
        if (Visible) visibleWindows.Add(this);
        foreach (var child in OwnedForms) if (child.Visible) visibleWindows.Add(child);
        try {
            SaveBounds();
            // Keep the WebView alive for its save acknowledgement, but remove
            // native windows immediately. Backend cleanup finishes while hidden.
            Hide();
            foreach (var child in visibleWindows) if (child != this) child.Hide();
            // ExecuteScriptAsync does not await JavaScript Promises. Use a small
            // explicit acknowledgement and poll it while the UI stays responsive.
            if (ready) {
            string request = json.Serialize(Guid.NewGuid().ToString());
            await view.CoreWebView2.ExecuteScriptAsync("window.__ezreadCloseResult=null;window.__ezreadCloseRequest=" + request + ";(async()=>{let result=false;try{result=typeof ezreadPrepareDesktopClose==='function'?await ezreadPrepareDesktopClose():true;}catch{}if(window.__ezreadCloseRequest===" + request + ")window.__ezreadCloseResult=result;})();");
            string answer = "null";
            for (int attempt = 0; attempt < 80 && answer == "null"; attempt++) {
                await Task.Delay(100);
                answer = await view.CoreWebView2.ExecuteScriptAsync("window.__ezreadCloseResult");
            }
            if (answer != "true") {
                RestoreClosingWindows(visibleWindows);
                MessageBox.Show(this, "仍有内容未能保存，窗口暂未关闭。请确认草稿已保存后重试。", "EzRead", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            }
            if (backend != null) await Task.Run((Action)backend.Stop);
            mayClose = true; Close();
        } catch (Exception exc) { mayClose = false; Log("close-save: " + exc.GetType().Name); RestoreClosingWindows(visibleWindows); MessageBox.Show(this, "无法完成关闭，窗口已恢复，请稍后重试。", "EzRead", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
        finally { preparingClose = false; }
    }
    private void RestoreClosingWindows(List<Form> windows) {
        foreach (var window in windows) if (!window.IsDisposed && !window.Disposing) window.Show();
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
        viewport.AutoScrollPosition = Point.Empty; ClientSize = client;
        RequestViewportFit(); await Task.Delay(350); await FitViewport(); await Task.Delay(200);
    }
    private async Task<object> SmokeLayout(string label) {
        string layout = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify({viewport:document.body.clientWidth,height:innerHeight,tracks:getComputedStyle(document.querySelector('#paper-grid')).gridTemplateColumns.split(/\\s+/).map(parseFloat),grid:document.querySelector('#paper-grid').getBoundingClientRect().toJSON(),container:document.querySelector('#library-view').getBoundingClientRect().toJSON(),sidebar:document.querySelector('.sidebar').getBoundingClientRect().width,footer:document.querySelector('.library-footer').getBoundingClientRect().toJSON(),dialog:[...document.querySelectorAll('dialog[open]')].map(n=>({id:n.id,...n.getBoundingClientRect().toJSON()})),cards:[...document.querySelectorAll('.paper-card')].map(n=>({id:n.dataset.paperId,x:n.offsetLeft,y:n.offsetTop,width:n.offsetWidth,height:n.offsetHeight,title:n.querySelector('.card-title').textContent}))})");
        using (var preview = new MemoryStream()) {
            await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, preview);
            preview.Position = 0;
            using (var bitmap = new Bitmap(preview)) {
                int left = Math.Max(0, -viewport.AutoScrollPosition.X), top = Math.Max(0, -viewport.AutoScrollPosition.Y);
                var crop = new Rectangle(left, top, Math.Min(viewport.ClientSize.Width, bitmap.Width - left), Math.Min(viewport.ClientSize.Height, bitmap.Height - top));
                using (var visible = bitmap.Clone(crop, bitmap.PixelFormat)) visible.Save(options.SmokeReport + "." + label + ".png", System.Drawing.Imaging.ImageFormat.Png);
            }
        }
        return new { label = label, clientWidth = viewport.ClientSize.Width, clientHeight = viewport.ClientSize.Height,
            viewWidth = view.Width, viewHeight = view.Height, minimumWidth = MinimumSize.Width, minimumHeight = MinimumSize.Height,
            scrollX = -viewport.AutoScrollPosition.X, scrollY = -viewport.AutoScrollPosition.Y,
            zoomFactor = view.ZoomFactor, layout = json.DeserializeObject(json.Deserialize<string>(layout)) };
    }
    private async Task SmokeTest(CoreWebView2Environment environment) {
        await Task.Delay(1600);
        var frame = SizeFromClientSize(Size.Empty);
        var layouts = new List<object>();
        foreach (var size in new[] { new Size(1366, 768), new Size(1920, 1080), new Size(2560, 1440) }) {
            await SetSmokeViewport(size, new Size(size.Width - frame.Width, size.Height - frame.Height));
            layouts.Add(await SmokeLayout(size.Width.ToString()));
        }
        var viewportChecks = new List<object>();
        var landscape = new Size(1920, 1080);
        int landscapeHeight = landscape.Height - frame.Height;
        await SetSmokeViewport(landscape, new Size(landscape.Width - frame.Width, landscapeHeight)); viewportChecks.Add(await SmokeLayout("normal-five"));
        foreach (var width in new[] { 1600, 1280, 1000 }) {
            await SetSmokeViewport(landscape, new Size(width, landscapeHeight)); viewportChecks.Add(await SmokeLayout("normal-" + width));
        }
        await SetSmokeViewport(landscape, new Size(1280, 540)); viewportChecks.Add(await SmokeLayout("flat"));
        viewport.AutoScrollPosition = new Point(0, 10000); await Task.Delay(150); viewportChecks.Add(await SmokeLayout("flat-scrolled"));
        var portrait = new Size(1080, 1920);
        await SetSmokeViewport(portrait, new Size(portrait.Width - frame.Width, portrait.Height - frame.Height)); viewportChecks.Add(await SmokeLayout("portrait"));
        await SetSmokeViewport(portrait, new Size(1, 1)); viewportChecks.Add(await SmokeLayout("portrait-minimum"));
        await view.CoreWebView2.ExecuteScriptAsync("openSettings()"); await Task.Delay(150); viewportChecks.Add(await SmokeLayout("clipped-settings"));
        viewport.AutoScrollPosition = new Point(10000, 10000); await Task.Delay(150); viewportChecks.Add(await SmokeLayout("clipped-settings-scrolled"));
        await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#settings-dialog').close()");
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
        var report = new { processId = Process.GetCurrentProcess().Id, browserProcessId = view.CoreWebView2.BrowserProcessId, webViewVersion = environment.BrowserVersionString, hostExecutable = Application.ExecutablePath, windowTitle = Text, bigIconPresent = big != IntPtr.Zero, bigIconOwned = big == Icon.Handle, bigIconMatchesBook = iconMatches, captionIconOwned = SendMessage(Handle, 0x7f, IntPtr.Zero, IntPtr.Zero) == captionIcon.Handle, page = json.DeserializeObject(json.Deserialize<string>(state)), layouts = layouts, viewportChecks = viewportChecks, settingsBackground = settingsBackground };
        File.WriteAllText(options.SmokeReport, json.Serialize(report), Encoding.UTF8);
        Close();
    }
    private void Log(string message) { try { Directory.CreateDirectory(options.Data); File.AppendAllText(Path.Combine(options.Data, "desktop.log"), DateTime.Now.ToString("s") + " " + message + Environment.NewLine, Encoding.UTF8); } catch { } }
    protected override void Dispose(bool disposing) {
        if (disposing) { viewportTimer.Dispose(); view.Dispose(); if (Icon != null) Icon.Dispose(); if (captionIcon != null) captionIcon.Dispose(); }
        base.Dispose(disposing);
    }
}
