"""Open EzRead in an independent Windows app window, without a console."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from ezread.config import data_directory as configured_data_directory, listen_port
from ezread.launching import StartupServer, run_guarded_server, startup_lock
from desktop_runtime import open_desktop, shell_command

ROOT = Path(__file__).resolve().parent
PORT = listen_port()
URL = f'http://127.0.0.1:{PORT}'
VERIFICATION_SESSION = None


def data_directory():
    return configured_data_directory(ROOT)


def _same_path(value, expected):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        actual = Path(value)
        return actual.is_absolute() and os.path.normcase(str(actual.resolve())) == os.path.normcase(str(expected.resolve()))
    except (OSError, ValueError):
        return False


def _read_status(endpoint):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(URL + endpoint, timeout=.8) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('response too large')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('invalid status response')
    return value


def _port_open():
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=.3):
            return True
    except OSError:
        return False


def running():
    """True only for this checkout and library; a different listener is an error."""
    expected = data_directory()
    legacy = False
    try:
        try:
            status = _read_status('/api/health')
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            # Old status responses can explain a conflict, but do not identify a checkout.
            legacy = True
            status = _read_status('/api/status')
    except (OSError, ValueError, urllib.error.URLError):
        if _port_open():
            raise RuntimeError(f'本机 {PORT} 端口已被其他服务占用或尚未正常响应。\n'
                               '请确认并关闭相应旧实例后重新打开 EzRead；启动器不会自动结束进程。') from None
        return False
    if not _same_path(status.get('data_dir'), expected):
        found = status.get('data_dir') if isinstance(status.get('data_dir'), str) else '无法识别'
        raise RuntimeError(f'{PORT} 端口上的服务使用另一处文献库，未打开该服务。\n'
                           f'当前项目应使用：{expected}\n已运行服务使用：{found}\n'
                           '请确认并关闭旧实例后重新打开 EzRead；启动器不会自动结束进程。')
    if legacy or not _same_path(status.get('app_root'), ROOT):
        raise RuntimeError(f'{PORT} 端口上的服务不是当前目录的 EzRead，或运行的是旧版本。\n'
                           f'当前程序目录：{ROOT}\n'
                           '请确认并关闭旧实例后重新打开；启动器不会自动结束进程。')
    return True


def ensure_server(wait_for_close=False):
    closing = wait_for_close
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            alive = running()
        except RuntimeError:
            if not closing:
                raise
            time.sleep(.1)
            continue
        if not alive:
            break
        # Identity was verified. It may exit before this second health read;
        # reconnect within the deadline instead of surfacing a socket error.
        closing = True
        try:
            state = _read_status('/api/health')
        except (OSError, ValueError, urllib.error.URLError):
            if not closing:
                raise
            time.sleep(.1)
            continue
        if not state.get('closing'):
            return
        closing = True
        time.sleep(.1)
    else:
        raise RuntimeError('EzRead 上一次退出尚未完成，请稍后重新打开。')
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    executable = Path(sys.executable)
    if executable.name.lower() == 'pythonw.exe':
        executable = executable.with_name('python.exe')
    if not executable.is_file():
        raise RuntimeError('未找到 Python 运行时，请查看 README.md。')
    owned = StartupServer()
    try:
        with (directory / 'server.log').open('a', encoding='utf-8') as log:
            owned.start(executable, ROOT, log)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if owned.process.poll() is not None:
                raise RuntimeError(f'EzRead 后台启动失败，请查看日志：\n{directory / "server.log"}')
            try:
                if running():
                    return owned
            except RuntimeError:
                # A freshly bound listener may not have its HTTP loop ready yet.
                pass
            time.sleep(.1)
        raise RuntimeError(f'EzRead 后台启动超时，请查看日志：\n{directory / "server.log"}')
    except BaseException:
        owned.cancel()
        raise


def open_window(report):
    return open_desktop(ROOT, data_directory(), URL, startup_report=report, verification_session=VERIFICATION_SESSION)


def wait_for_window(process, report, timeout=25):
    try:
        return _wait_for_window(process, report, timeout)
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        raise


def _wait_for_window(process, report, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            if code in (0, 3):
                return code  # Existing window activated, or closing: retain the request.
            raise RuntimeError('EzRead 桌面窗口启动失败，请查看 desktop.log。')
        if report.is_file():
            status = json.loads(report.read_text(encoding='utf-8-sig'))
            if status.get('ready') is True and status.get('process_id') == process.pid:
                return 0
            raise RuntimeError('EzRead 桌面窗口未能完成初始化。')
        time.sleep(.05)
    # Only this just-created native process is owned by this startup attempt.
    process.kill()
    process.wait(timeout=3)
    raise RuntimeError('EzRead 桌面窗口启动超时，已停止本次启动。')


def start_application():
    directory = data_directory()
    with startup_lock(ROOT, directory, URL):
        shell_command(ROOT, directory, URL)  # Missing components must not create a server.
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='desktop-start-', dir=directory) as folder:
            report = Path(folder) / 'ready.json'
            deadline = time.monotonic() + 45
            deferred = False
            while time.monotonic() < deadline:
                owned = ensure_server(wait_for_close=deferred)
                try:
                    code = wait_for_window(open_window(report), report)
                    if code == 0:
                        if owned is not None:
                            owned.commit()
                        return
                except BaseException:
                    if owned is not None:
                        owned.cancel()
                    raise
                if owned is not None:
                    owned.cancel()
                deferred = True
                # A closing host explicitly declined activation. Wait and retry;
                # a cancelled close activates the restored window instead.
                time.sleep(.15)
            raise RuntimeError('EzRead 上一次关闭尚未完成，重新打开请求已超时，请稍后重试。')


def main():
    try:
        start_application()
    except Exception as exc:
        if VERIFICATION_SESSION is not None:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(exc), 'EzRead', 0x10)
        except Exception:
            raise
        return 1
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--server':
        run_guarded_server(ROOT, sys.argv[2], sys.argv[3])
    else:
        if len(sys.argv) == 3 and sys.argv[1] == '--verification-session':
            VERIFICATION_SESSION = uuid.UUID(sys.argv[2]).hex
        sys.exit(main())
