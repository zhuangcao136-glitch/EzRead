"""Verify native close/reopen and crash cleanup with an isolated real backend."""
import argparse
import contextlib
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path, required=True)
    args = parser.parse_args()
    executable = args.executable.resolve()
    if os.name != 'nt' or not executable.is_relative_to(ROOT):
        raise SystemExit('Use a Windows desktop build inside this workspace.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    def handle(pid, missing_ok=False):
        value = kernel.OpenProcess(0x100000, False, pid)
        if not value:
            if missing_ok and ctypes.get_last_error() == 87: return None
            raise ctypes.WinError(ctypes.get_last_error())
        return value
    def exited(value, timeout=4000):
        return kernel.WaitForSingleObject(value, timeout) == 0
    def descendants(pid):
        class Entry(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                        ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                        ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD), ('name', wintypes.WCHAR * 260)]
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        edges = []
        try:
            found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while found:
                edges.append((entry.pid, entry.parent)); found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally: kernel.CloseHandle(snapshot)
        owned = {pid}
        while True:
            following = {child for child, parent in edges if parent in owned} | owned
            if following == owned: return owned - {pid}
            owned = following
    checks = []
    with tempfile.TemporaryDirectory(prefix='exit-native-', dir=ROOT / 'work', ignore_cleanup_errors=True) as folder:
        data = Path(folder)
        app_root = data / 'app'; binary = app_root / 'desktop/bin'; static = app_root / 'static'
        binary.mkdir(parents=True); static.mkdir()
        for file in executable.parent.iterdir():
            if file.is_file(): shutil.copy2(file, binary / file.name)
        for name in ('ezread.ico', 'window-icon-transparent.ico'): shutil.copy2(ROOT / 'static' / name, static / name)
        native_executable = binary / 'EzRead.Desktop.exe'
        environment = {**os.environ, 'EZREAD_DATA_DIR': str(data), 'EZREAD_TEST_APP_ROOT': str(app_root),
                       'NO_PROXY': '127.0.0.1,localhost', 'PYTHONIOENCODING': 'utf-8'}
        instances = []
        for cycle in range(4):
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
            origin = f'http://127.0.0.1:{port}'
            def request(path, body=None):
                raw = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(origin + path, data=raw, headers={'Content-Type': 'application/json'})
                with opener.open(req, timeout=3) as response: return json.load(response)
            with (data / f'backend-{cycle}.log').open('w', encoding='utf-8') as log:
                backend = subprocess.Popen([sys.executable, str(ROOT / 'tests/backend_exit_fixture.py'), '--port', str(port)],
                                           cwd=ROOT, env=environment, stdout=log, stderr=log,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                window = None; handles = []
                try:
                    for _ in range(100):
                        if backend.poll() is not None: raise RuntimeError((data / f'backend-{cycle}.log').read_text(encoding='utf-8', errors='replace'))
                        try: health = request('/api/health'); break
                        except (OSError, urllib.error.URLError): time.sleep(.05)
                    else: raise RuntimeError('Temporary backend did not start.')
                    instances.append(health['instance_id'])
                    assert health['pid'] == backend.pid and Path(health['data_dir']).resolve() == data.resolve()
                    progress = request('/api/papers/0123456789abcdef/selection-progress?client_id=' + 'a' * 32 + '&request_id=' + 'b' * 32)
                    assert progress == {'events': [], 'output': '', 'done': False}
                    children = request('/__test/spawn', {})
                    handles = [handle(children[name]) for name in ('child_pid', 'grandchild_pid')]
                    if cycle == 2:
                        backend.terminate(); backend.wait(timeout=5)
                        assert all(exited(value) for value in handles), 'Crash left model descendants alive.'
                        checks.append({'backendCrashKillsDescendants': True})
                        continue
                    report = data / f'native-{cycle}.json'
                    log_path = data / 'desktop.log'
                    log_size = log_path.stat().st_size if log_path.exists() else 0
                    window = subprocess.Popen([str(native_executable), '--app-root', str(app_root), '--data-dir', str(data),
                                               '--url', origin, '--smoke-test', str(report)], cwd=ROOT,
                                              creationflags=subprocess.CREATE_NO_WINDOW)
                    if cycle == 3:
                        for _ in range(100):
                            attached = request('/__test/lifetime')['attached']
                            content = log_path.read_bytes()[log_size:] if log_path.exists() else b''
                            if attached and b'control-created' in content: break
                            if window.poll() is not None: raise RuntimeError('Native host exited before crash verification.')
                            time.sleep(.05)
                        else: raise RuntimeError('Native host was not attached to its backend.')
                        browsers = [handle(pid) for pid in descendants(window.pid)]
                        assert browsers, 'Native host had no WebView2 descendants to verify.'
                        window.terminate(); window.wait(timeout=5)
                        assert backend.wait(timeout=8) == 0, 'Host crash left its backend alive.'
                        assert all(exited(value) for value in browsers + handles), 'Host crash left owned processes alive.'
                        for value in browsers: kernel.CloseHandle(value)
                        checks.append({'nativeCrashKillsBackendAndWebView2': True})
                        continue
                    assert window.wait(timeout=45) == 0, 'Native window did not close cleanly.'
                    native = json.loads(report.read_text(encoding='utf-8-sig'))
                    browser = handle(native['browserProcessId'], missing_ok=True) if not native.get('error') else None
                    if browser:
                        assert exited(browser), 'WebView2 stayed alive after window exit.'
                        kernel.CloseHandle(browser)
                    assert backend.wait(timeout=5) == 0, 'Backend stayed alive after native close.'
                    assert all(exited(value) for value in handles), 'Model descendants stayed alive after close.'
                    with contextlib.closing(sqlite3.connect(data / 'library.sqlite3')) as connection:
                        doc = json.loads(connection.execute('SELECT doc FROM papers WHERE id=?', ('0123456789abcdef',)).fetchone()[0])
                    assert doc['notes'] == '关闭前已保存的测试笔记'
                    assert doc['translation']['status'] == 'paused'
                    assert doc['translation']['staging']['one'] == '已保留的测试重译草稿'
                    checks.append({'cycle': cycle + 1, 'backendPid': backend.pid, 'allOwnedProcessesExited': True, 'draftAndNotesPreserved': True, 'progressInterfaceReady': True})
                finally:
                    if window and window.poll() is None: window.terminate(); window.wait(timeout=5)
                    if backend.poll() is None: backend.terminate(); backend.wait(timeout=5)
                    for value in handles: kernel.CloseHandle(value)
        assert len(set(instances)) == 4, 'Reopen reused an old backend instance.'
    result = {'checks': checks, 'freshBackendOnReopen': True, 'realModelCalls': 0, 'realLibraryWrites': 0}
    (ROOT / 'work/exit-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
