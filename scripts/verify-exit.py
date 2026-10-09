"""Verify native hide-before-save, close/reopen and isolated crash cleanup."""
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
    user = ctypes.WinDLL('user32', use_last_error=True)
    window_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [window_callback, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.IsZoomed.argtypes = [wintypes.HWND]
    user.IsZoomed.restype = wintypes.BOOL
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.EnumChildWindows.argtypes = [wintypes.HWND, window_callback, wintypes.LPARAM]
    user.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    def visible_windows(pid):
        windows = []
        @window_callback
        def collect(hwnd, unused):
            owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user.IsWindowVisible(hwnd): windows.append(hwnd)
            return True
        if not user.EnumWindows(collect, 0): raise ctypes.WinError(ctypes.get_last_error())
        return windows
    def window_class(hwnd):
        value = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(hwnd, value, len(value))
        return value.value
    def dialog_controls(hwnd):
        controls = []
        @window_callback
        def collect(control, unused):
            value = ctypes.create_unicode_buffer(1024)
            user.GetWindowTextW(control, value, len(value))
            controls.append({'class': window_class(control), 'id': user.GetDlgCtrlID(control), 'text': value.value})
            return True
        user.EnumChildWindows(hwnd, collect, 0)
        return controls
    def post(hwnd, message, value=0, control=0):
        if not user.PostMessageW(hwnd, message, value, control): raise ctypes.WinError(ctypes.get_last_error())
    def wait_until(test, timeout, message):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            value = test()
            if value: return value
            time.sleep(.005)
        raise AssertionError(message)
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
    (ROOT / 'work').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='exit-native-', dir=ROOT / 'work', ignore_cleanup_errors=True) as folder:
        data = Path(folder)
        app_root = data / 'app'; binary = app_root / 'desktop/bin'; static = app_root / 'static'
        binary.mkdir(parents=True); static.mkdir()
        for file in executable.parent.iterdir():
            if file.is_file(): shutil.copy2(file, binary / file.name)
        for name in ('ezread.ico', 'window-icon-transparent.ico'): shutil.copy2(ROOT / 'static' / name, static / name)
        native_executable = binary / 'EzRead.Desktop.exe'
        environment = {**os.environ, 'EZREAD_DATA_DIR': str(data), 'EZREAD_TEST_APP_ROOT': str(app_root),
                       'EZREAD_TEST_CLOSE_DELAY_MS': '1000',
                       'NO_PROXY': '127.0.0.1,localhost', 'PYTHONIOENCODING': 'utf-8'}
        instances = []
        crash_notes = '关闭中浏览器异常前保留的测试草稿'
        crash_port = None
        for cycle in range(7):
            notes = f'关闭握手保存的测试笔记 {cycle + 1}'
            environment.update(EZREAD_TEST_CLOSE_NOTES=notes, EZREAD_TEST_CLOSE_FAIL_ONCE='1' if cycle == 0 else '0',
                               EZREAD_TEST_CRASH_NOTES=crash_notes if cycle == 4 else '',
                               EZREAD_TEST_BLOCK_CHECKPOINT='1' if cycle == 6 else '0')
            if cycle == 5:
                port = crash_port  # Production reopens at the same localStorage origin.
            else:
                with socket.socket() as reservation:
                    reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
                if cycle == 4: crash_port = port
            origin = f'http://127.0.0.1:{port}'
            def request(path, body=None):
                raw = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(origin + path, data=raw, headers={'Content-Type': 'application/json'})
                with opener.open(req, timeout=3) as response: return json.load(response)
            with (data / f'backend-{cycle}.log').open('w', encoding='utf-8') as log:
                backend = subprocess.Popen([sys.executable, str(ROOT / 'tests/backend_exit_fixture.py'), '--port', str(port)],
                                           cwd=ROOT, env=environment, stdout=log, stderr=log,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                window = None; duplicate = None; handles = []; browsers = []
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
                    log_path = data / 'desktop.log'
                    log_size = log_path.stat().st_size if log_path.exists() else 0
                    command = [str(native_executable), '--app-root', str(app_root), '--data-dir', str(data), '--url', origin]
                    window = subprocess.Popen(command, cwd=ROOT,
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
                        checks.append({'nativeCrashKillsBackendAndWebView2': True})
                        continue
                    def ready_window():
                        if window.poll() is not None: raise RuntimeError('Native host exited before close verification.')
                        content = log_path.read_bytes()[log_size:] if log_path.exists() else b''
                        state = request('/__test/close-state')
                        candidates = [hwnd for hwnd in visible_windows(window.pid) if not user.GetWindow(hwnd, 4)]
                        return candidates[0] if state['ready'] and b'ready WebView2=' in content and candidates else None
                    hwnd = wait_until(ready_window, 20, 'Native page/window did not become ready.')
                    if cycle == 1:
                        user.ShowWindow(hwnd, 3)
                        wait_until(lambda: user.IsZoomed(hwnd), 1, 'Test window did not maximize.')
                    if cycle == 5:
                        assert request('/__test/close-state')['loadedNotes'] == crash_notes, 'Reopen lost the locally preserved crash draft.'
                    browsers = [handle(pid) for pid in descendants(window.pid)]
                    assert browsers, 'Native host had no WebView2 descendants to verify.'
                    if cycle in (4, 6):
                        post(hwnd, 0x10)
                        wait_until(lambda: not visible_windows(window.pid), .25, 'Crash close did not hide its window.')
                        def checkpoint_ready():
                            content = log_path.read_bytes()[log_size:]
                            expected = b'close-local-drafts=True' if cycle == 4 else b'close-local-drafts=False'
                            return expected in content and request('/__test/close-state')['attempts']
                        wait_until(checkpoint_ready, 2, 'Close did not preserve local drafts before saving.')
                        # Stable handles belong only to WebView2 descendants of
                        # this temporary native host, never to the user's apps.
                        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
                        for pid in descendants(window.pid):
                            target = kernel.OpenProcess(0x1 | 0x100000, False, pid)
                            if not target: continue
                            try: kernel.TerminateProcess(target, 1)
                            finally: kernel.CloseHandle(target)
                        if cycle == 6:
                            def recovered_dialog():
                                visible = visible_windows(window.pid)
                                dialogs = [item for item in visible if window_class(item) == '#32770']
                                return dialogs[0] if hwnd in visible and dialogs and request('/__test/close-state').get('readyLoads', 0) >= 2 else None
                            dialog = wait_until(recovered_dialog, 10, 'Unconfirmed crash did not recreate a usable page before warning.')
                            post(dialog, 0x10)
                            wait_until(lambda: not user.IsWindowVisible(dialog), 1, 'Recovery warning did not close.')
                            assert window.poll() is None and backend.poll() is None
                            assert b'close-recovery-recreated-webview' in log_path.read_bytes()[log_size:]
                            post(hwnd, 0x10)
                            assert window.wait(timeout=15) == 0
                            assert backend.wait(timeout=5) == 0
                            assert all(exited(value) for value in browsers + handles)
                            checks.append({'unconfirmedCrashRecreatesUsableWindow': True, 'retryCloseExits': True})
                            continue
                        deadline = time.perf_counter() + 15
                        while window.poll() is None and time.perf_counter() < deadline:
                            assert not visible_windows(window.pid), 'WebView2 crash resurrected a window or modal warning during close.'
                            time.sleep(.005)
                        assert window.wait(timeout=1) == 0
                        assert backend.wait(timeout=5) == 0
                        assert all(exited(value) for value in browsers + handles)
                        assert b'close-with-local-drafts-after-webview-failure' in log_path.read_bytes()[log_size:]
                        checks.append({'webviewCrashDuringCloseStaysHidden': True, 'allOwnedProcessesExited': True})
                        continue
                    hidden_times = []
                    for attempt in range(1, 3 if cycle == 0 else 2):
                        assert user.IsWindowVisible(hwnd), 'Main window was not visible before close.'
                        started = time.perf_counter()
                        post(hwnd, 0x10)  # WM_CLOSE, only the HWND from this isolated PID.
                        wait_until(lambda: not visible_windows(window.pid), .25, 'Close left a native window visible for over 250 ms.')
                        hidden_times.append(round((time.perf_counter() - started) * 1000, 2))
                        wait_until(lambda: len(request('/__test/close-state')['attempts']) >= attempt, .5, 'Close did not invoke the save hook.')
                        if cycle != 0 or attempt != 1:
                            duplicate = subprocess.Popen(command, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
                        time.sleep(.25)
                        probe = request('/__test/close-state')['attempts'][attempt - 1]
                        assert 'finished' not in probe, 'Delayed save hook had already completed during visibility check.'
                        assert window.poll() is None and backend.poll() is None, 'Hiding terminated the host/backend before saving.'
                        assert all(not exited(value, 0) for value in handles), 'Hiding terminated model processes before saving.'
                        assert any(not exited(value, 0) for value in browsers), 'Hiding terminated WebView2 before saving.'
                        assert not visible_windows(window.pid), 'Window reappeared while saving.'
                        if cycle == 0 and attempt == 1:
                            def restored_dialog():
                                visible = visible_windows(window.pid)
                                dialogs = [item for item in visible if window_class(item) == '#32770']
                                return dialogs[0] if hwnd in visible and dialogs else None
                            dialog = wait_until(restored_dialog, 4, 'Failed save did not restore its main window and warning.')
                            failed = request('/__test/close-state')['attempts'][0]
                            assert failed['saved'] and failed['result'] is False
                            (data / 'native-dialog.json').write_text(json.dumps({'class': window_class(dialog),
                                'controls': dialog_controls(dialog)}, ensure_ascii=False, indent=2), encoding='utf-8')
                            post(dialog, 0x10)  # WM_CLOSE only on this test PID's warning dialog.
                            wait_until(lambda: not user.IsWindowVisible(dialog), 1, 'Test warning did not close.')
                            assert user.IsWindowVisible(hwnd), 'Dismissing warning hid the restored window.'
                        else:
                            assert duplicate.wait(timeout=3) == 0, 'Duplicate startup failed while closing.'
                            assert window.poll() is None and backend.poll() is None, 'Save delay ended before duplicate-wake verification.'
                            assert not visible_windows(window.pid), 'Duplicate startup showed the window during close.'
                    assert window.wait(timeout=15) == 0, 'Native window did not close cleanly.'
                    assert all(exited(value) for value in browsers), 'WebView2 stayed alive after window exit.'
                    assert backend.wait(timeout=5) == 0, 'Backend stayed alive after native close.'
                    assert all(exited(value) for value in handles), 'Model descendants stayed alive after close.'
                    saved_close = json.loads((data / 'close-state.json').read_text(encoding='utf-8'))
                    assert saved_close['attempts'][-1]['result'] is True
                    assert all(item['saved'] and item['finished'] - item['started'] >= .95 for item in saved_close['attempts'])
                    with contextlib.closing(sqlite3.connect(data / 'library.sqlite3')) as connection:
                        doc = json.loads(connection.execute('SELECT doc FROM papers WHERE id=?', ('0123456789abcdef',)).fetchone()[0])
                    assert doc['notes'] == f'{notes}，第 {len(saved_close["attempts"])} 次'
                    assert doc['blocks'][0]['text'] == 'Synthetic source.' and doc['blocks'][0]['translation'] == '已保存的测试译文'
                    assert doc['translation']['status'] == 'paused'
                    assert doc['translation']['staging']['one'] == '已保留的测试重译草稿'
                    assert (data / 'desktop-window.json').is_file(), 'Close did not preserve native window bounds.'
                    checks.append({'cycle': cycle + 1, 'backendPid': backend.pid, 'hiddenWithinMs': hidden_times,
                                   'aliveWhileSavePending': True, 'duplicateWakeStaysHidden': True,
                                   'failedSaveRestoresWindow': cycle == 0, 'allOwnedProcessesExited': True,
                                   'draftAndNotesPreserved': True, 'pendingNoteSavedAfterHide': True, 'progressInterfaceReady': True})
                    if cycle == 5: checks[-1]['crashDraftRecoveredOnReopen'] = True
                    if cycle == 1: checks[-1]['closedFromMaximized'] = True
                except BaseException:
                    failure = ROOT / 'work/exit-verification-failure'
                    failure.mkdir(exist_ok=True)
                    for file in data.glob('*.log'): shutil.copy2(file, failure / file.name)
                    close_state = data / 'close-state.json'
                    if close_state.is_file(): shutil.copy2(close_state, failure / close_state.name)
                    native_dialog = data / 'native-dialog.json'
                    if native_dialog.is_file(): shutil.copy2(native_dialog, failure / native_dialog.name)
                    raise
                finally:
                    if duplicate and duplicate.poll() is None: duplicate.terminate(); duplicate.wait(timeout=5)
                    if window and window.poll() is None: window.terminate(); window.wait(timeout=5)
                    if backend.poll() is None: backend.terminate(); backend.wait(timeout=5)
                    for value in browsers + handles: kernel.CloseHandle(value)
        assert len(set(instances)) == 7, 'Reopen reused an old backend instance.'
    result = {'checks': checks, 'freshBackendOnReopen': True, 'realModelCalls': 0, 'realLibraryWrites': 0}
    (ROOT / 'work/exit-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
