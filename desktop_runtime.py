"""Start the native WebView2 window without an external browser fallback."""
import os
from pathlib import Path
import subprocess
import time


def shell_command(root, directory, url):
    root, directory = Path(root).resolve(), Path(directory).resolve()
    executable = root / 'desktop' / 'bin' / 'EzRead.Desktop.exe'
    required = [executable, executable.with_name('Microsoft.Web.WebView2.Core.dll'),
                executable.with_name('Microsoft.Web.WebView2.WinForms.dll'), executable.with_name('WebView2Loader.dll')]
    if not all(path.is_file() for path in required):
        raise RuntimeError('EzRead 桌面组件尚未构建。请先运行：\n'
                           'powershell -File scripts/build-desktop.ps1\n'
                           '启动器不会退回 Edge 或 Chrome 浏览器窗口。')
    return [str(executable), '--app-root', str(root), '--data-dir', str(directory), '--url', url]


def open_desktop(root, directory, url, startup_report=None, verification_session=None):
    if os.name != 'nt':
        raise RuntimeError('此桌面启动器目前支持 Windows；其他系统可运行 server.py 并在浏览器阅读。')
    command = shell_command(root, directory, url)
    if startup_report is not None:
        command += ['--startup-report', str(startup_report)]
    if verification_session is not None:
        command += ['--verification-session', verification_session]
    from browser_state import prepare_migration
    try:
        prepare_migration(directory, url.rstrip('/'))
    except (OSError, ValueError, UnicodeError) as exc:
        # Keep the entire old profile. Failure must not invent or overwrite drafts.
        with (Path(directory) / 'desktop.log').open('a', encoding='utf-8') as log:
            log.write(f'{time.strftime("%Y-%m-%dT%H:%M:%S")} legacy-storage-migration: {type(exc).__name__}\n')
    return subprocess.Popen(command, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
