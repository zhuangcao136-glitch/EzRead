"""Open EzRead in an independent Windows app window, without a console."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from ezread.config import data_directory as configured_data_directory, listen_port

ROOT = Path(__file__).resolve().parent
PORT = listen_port()
URL = f'http://127.0.0.1:{PORT}'


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


def ensure_server():
    closing = False
    for _ in range(100):
        try:
            alive = running()
        except RuntimeError:
            if not closing:
                raise
            time.sleep(.1)
            continue
        if not alive:
            break
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
    with (directory / 'server.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen(
            [str(executable), str(ROOT / 'server.py')], cwd=ROOT,
            stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(120):
        if running():
            return
        if process.poll() is not None:
            raise RuntimeError(f'EzRead 后台启动失败，请查看日志：\n{directory / "server.log"}')
        time.sleep(.15)
    raise RuntimeError(f'EzRead 启动超时，请查看日志：\n{directory / "server.log"}\n'
                       f'也请确认 {PORT} 端口未被其他实例占用。')


def open_window():
    from desktop_runtime import open_desktop
    return open_desktop(ROOT, data_directory(), URL)


def main():
    try:
        ensure_server()
        open_window()
    except Exception as exc:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(exc), 'EzRead', 0x10)
        except Exception:
            raise
        return 1
    return 0


if __name__ == '__main__':
    main()
