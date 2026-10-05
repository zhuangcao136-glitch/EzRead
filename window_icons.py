"""Hide EzRead's caption icon without replacing the browser-owned taskbar icon.

Only top-level windows of the browser's exact EzRead profile are eligible. The
helper owns its caption HICON until those windows close; it never changes browser files,
class icons, window titles, or icons belonging to other browser profiles.
"""
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

WM_GETICON, WM_SETICON, ICON_SMALL, ICON_BIG = 0x7f, 0x80, 0, 1


def matching_browser_processes(processes, profile, parse_command):
    """Match the full profile argument, not a title, path prefix, or URL alone."""
    wanted = os.path.normcase(str(Path(profile).resolve()))
    result = set()
    for process in processes:
        args = parse_command(process.get('CommandLine') or '')
        if not args or Path(args[0]).name.lower() not in ('msedge.exe', 'chrome.exe'):
            continue
        if any(arg.startswith('--type=') for arg in args):
            continue
        for arg in args[1:]:
            if arg.startswith('--user-data-dir='):
                actual = arg.split('=', 1)[1]
                if actual and os.path.normcase(str(Path(actual).resolve())) == wanted:
                    result.add(int(process['ProcessId']))
    return result


class WindowIcons:
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback_type, W.LPARAM]
        self.user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
        self.user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        self.user.IsWindowVisible.argtypes = [W.HWND]
        self.user.GetWindow.argtypes = [W.HWND, W.UINT]
        self.user.GetWindow.restype = W.HWND
        self.user.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT]
        self.user.LoadImageW.restype = W.HANDLE
        self.user.DestroyIcon.argtypes = [W.HICON]
        self.user.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM,
                                                 W.UINT, W.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.user.SendMessageTimeoutW.restype = W.LPARAM
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
        self.kernel.CreateMutexW.restype = W.HANDLE
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.shell.CommandLineToArgvW.argtypes = [W.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        self.shell.CommandLineToArgvW.restype = ctypes.POINTER(W.LPWSTR)

    def command_args(self, command):
        if not command:
            return []
        count = ctypes.c_int()
        argv = self.shell.CommandLineToArgvW(command, ctypes.byref(count))
        if not argv:
            return []
        try:
            return [argv[i] for i in range(count.value)]
        finally:
            self.kernel.LocalFree(argv)

    def browser_pids(self, profile):
        # No path interpolation into shell code. CIM results are filtered again
        # using Windows' command-line parser before any window is modified.
        script = r"""$ErrorActionPreference='Stop';
        $OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);
        @(Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe' OR Name = 'chrome.exe'" |
          Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($env:EZREAD_ICON_PROFILE,[StringComparison]::OrdinalIgnoreCase) -ge 0 } |
          Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress"""
        environment = dict(os.environ, EZREAD_ICON_PROFILE=str(profile))
        powershell = Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
                                env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('Cannot identify the EzRead browser profile')
        # Windows PowerShell may use the active OEM code page. Force UTF-8
        # output explicitly so Chinese installation/profile paths round-trip.
        raw = result.stdout.decode('utf-8-sig').strip()
        rows = json.loads(raw) if raw else []
        if isinstance(rows, dict):
            rows = [rows]
        return matching_browser_processes(rows, profile, self.command_args)

    def windows(self, pids):
        result = []
        @self.callback_type
        def visit(hwnd, _):
            pid = W.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and self.user.IsWindowVisible(hwnd) and not self.user.GetWindow(hwnd, 4):
                name = ctypes.create_unicode_buffer(256)
                self.user.GetClassNameW(hwnd, name, len(name))
                if name.value == 'Chrome_WidgetWin_1':
                    result.append(hwnd)
            return True
        self.user.EnumWindows(visit, 0)
        return result

    def message(self, hwnd, message, kind, value=0):
        result = ctypes.c_size_t()
        if not self.user.SendMessageTimeoutW(hwnd, message, kind, value, 2, 250, ctypes.byref(result)):
            raise OSError('Browser window did not accept the icon message')
        return result.value

    def load_icon(self, path, size=32):
        icon = self.user.LoadImageW(None, str(path), 1, size, size, 0x10)
        if not icon:
            raise ctypes.WinError(ctypes.get_last_error())
        return icon


def follow_browser_windows(api, profile, caption_icon, *, clock=time.monotonic,
                           pause=time.sleep, log=lambda _: None):
    """Reacquire the exact profile when a browser exits and quickly restarts."""
    deadline, pids = clock() + 20, set()
    while not pids and clock() < deadline:
        pids = api.browser_pids(profile)
        if not pids:
            pause(.4)
    if not pids:
        log('no-browser')
        return
    log('watching-pids ' + ','.join(map(str, sorted(pids))))
    seen, missing_since = False, None
    while True:
        windows = api.windows(pids)
        if not windows:
            # A duplicate launcher can see our mutex and exit while the old
            # browser is closing. Discover its replacement before releasing it.
            current_pids = api.browser_pids(profile)
            if current_pids != pids:
                pids = current_pids
                log('new-browser-pids ' + ','.join(map(str, sorted(pids))))
            windows = api.windows(pids)
        if windows:
            seen, missing_since = True, None
            for hwnd in windows:
                try:
                    if api.message(hwnd, WM_GETICON, ICON_SMALL) != caption_icon:
                        api.message(hwnd, WM_SETICON, ICON_SMALL, caption_icon)
                except OSError:
                    continue  # The window may close between enumeration and send.
        else:
            if missing_since is None:
                missing_since = clock()
            if clock() - missing_since > (3 if seen else 20):
                break
        pause(.75)


def maintain_taskbar_icon(root, profile):
    """One caption owner per profile. Real favicon stays valid if it exits."""
    if os.name != 'nt':
        return
    api = WindowIcons()
    identity = hashlib.sha256(os.path.normcase(str(Path(profile).resolve())).encode('utf-8')).hexdigest()[:24]
    ctypes.set_last_error(0)
    mutex = api.kernel.CreateMutexW(None, False, 'Local\\EzRead.TaskbarIcon.' + identity)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    already_running = ctypes.get_last_error() == 183
    icon = None
    def log_event(event):
        try:
            folder = Path(root) / 'work'
            folder.mkdir(exist_ok=True)
            with (folder / 'window-icon-lifecycle.log').open('a', encoding='utf-8') as output:
                output.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} pid={os.getpid()} {event}\n')
        except OSError:
            pass
    try:
        if already_running:
            log_event('already-running')
            return
        icon = api.load_icon(Path(root) / 'static/window-icon-transparent.ico', 16)
        log_event('started')
        follow_browser_windows(api, profile, icon, log=log_event)
    finally:
        # HICON remains valid for the whole lifetime of the windows using it.
        if icon:
            api.user.DestroyIcon(icon)
            log_event('stopped')
        api.kernel.CloseHandle(mutex)


if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    maintain_taskbar_icon(root, root / 'data/browser-profile')
