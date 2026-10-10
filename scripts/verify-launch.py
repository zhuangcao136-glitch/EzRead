"""Exercise concurrent startup and queued reopen with isolated native windows."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=Path(sys.executable), help='Also exercise a venv python.exe redirector.')
    args = parser.parse_args()
    executable = args.executable.resolve()
    if os.name != 'nt' or not executable.is_relative_to(ROOT):
        raise SystemExit('Use a Windows desktop build inside this workspace.')
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    handles = []
    launchers = []

    def retain(pid):
        handle = kernel.OpenProcess(0x100001, False, pid)
        if not handle: raise ctypes.WinError(ctypes.get_last_error())
        handles.append(handle)
        return handle

    def wait(test, seconds=25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = test()
            if result: return result
            time.sleep(.05)
        raise AssertionError('Timed out waiting for isolated launch verification.')

    def window(pid):
        found = []
        @callback
        def collect(hwnd, unused):
            owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(hwnd, name, len(name))
            if owner.value == pid and user.IsWindowVisible(hwnd) and name.value.startswith('WindowsForms10.Window.'):
                found.append(hwnd)
            return True
        user.EnumWindows(collect, 0)
        return found[0] if found else None

    checks = []
    (ROOT / 'work').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='launch-native-', dir=ROOT / 'work', ignore_cleanup_errors=True) as folder:
        data = Path(folder); app = data / 'app'; binary = app / 'desktop/bin'
        binary.mkdir(parents=True); (app / 'static').mkdir()
        for path in executable.parent.iterdir():
            if path.is_file(): shutil.copy2(path, binary / path.name)
        for name in ('ezread.ico', 'window-icon-transparent.ico'):
            shutil.copy2(ROOT / 'static' / name, app / 'static' / name)
        for name in ('launch.pyw', 'desktop_runtime.py', 'browser_state.py'):
            shutil.copy2(ROOT / name, app / name)
        shutil.copytree(ROOT / 'ezread', app / 'ezread', ignore=shutil.ignore_patterns('__pycache__'))
        (app / 'server.py').write_text('import runpy\nrunpy.run_path(' + repr(str(ROOT / 'tests/backend_exit_fixture.py')) + ', run_name="__main__")\n', encoding='utf-8')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'EZREAD_DATA_DIR': str(data), 'EZREAD_TEST_APP_ROOT': str(app), 'EZREAD_PORT': str(port),
               'EZREAD_TEST_CLOSE_DELAY_MS': '1500', 'EZREAD_TEST_RELOAD_AFTER_READY': '1',
               'NO_PROXY': '127.0.0.1,localhost', 'PYTHONIOENCODING': 'utf-8'}
        session = uuid.uuid4().hex
        command = [str(args.python.resolve()), str(app / 'launch.pyw'), '--verification-session', session]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(path, body=None):
            raw = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(origin + path, data=raw, headers={'Content-Type': 'application/json'})
            try:
                with opener.open(req, timeout=.8) as response: return json.load(response)
            except (OSError, urllib.error.URLError): return None

        def host():
            log = data / 'desktop.log'
            if not log.exists(): return None
            pids = re.findall(r'pid=(\d+) child=False ready WebView2=', log.read_text(encoding='utf-8-sig'))
            return int(pids[-1]) if pids else None

        with (data / 'launcher.log').open('w', encoding='utf-8') as output:
            def launch():
                process = subprocess.Popen(command, env=env, cwd=app, stdout=output, stderr=output,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                launchers.append(process)
                return process
            try:
                first = [launch() for _ in range(3)]
                startup_codes = [p.wait(timeout=40) for p in first]
                health = request('/api/health'); old_host = host()
                if health: backend = retain(health['pid'])
                if old_host: native = retain(old_host)
                assert startup_codes == [0, 0, 0], 'Concurrent startup failed.'
                old_instance = health['instance_id']
                hwnd = wait(lambda: window(old_host))
                wait(lambda: (request('/__test/close-state') or {}).get('readyLoads', 0) >= 2)
                assert kernel.WaitForSingleObject(native, 0) != 0
                assert request('/api/health')['instance_id'] == old_instance
                checks.append({'reloadAfterStartupReportRemovedPreservesHost': True})
                log = (data / 'desktop.log').read_text(encoding='utf-8-sig')
                assert log.count(' child=False initializing') == 1, 'Concurrent launch created multiple native hosts.'
                assert 'EzRead.Desktop.Verification.' + session + ' taskbar=False' in log
                checks.append({'concurrentLaunches': 3, 'oneBackendAndHost': True})
                user.PostMessageW(hwnd, 0x10, 0, 0)
                wait(lambda: (request('/__test/close-state') or {}).get('attempts'))
                reopening = [launch() for _ in range(3)]
                time.sleep(.2)
                assert not user.IsWindowVisible(hwnd), 'Queued reopen resurrected the saving window.'
                reopen_codes = [p.wait(timeout=40) for p in reopening]
                fresh = request('/api/health')
                fresh_host = host()
                if fresh: fresh_backend = retain(fresh['pid'])
                if fresh_host and fresh_host != old_host: fresh_native = retain(fresh_host)
                assert reopen_codes == [0, 0, 0], 'Queued reopen did not complete.'
                assert fresh['instance_id'] != old_instance
                assert kernel.WaitForSingleObject(backend, 1000) == 0
                assert kernel.WaitForSingleObject(native, 1000) == 0
                fresh_hwnd = wait(lambda: window(fresh_host))
                wait(lambda: (request('/__test/close-state') or {}).get('readyLoads', 0) >= 2)
                log = (data / 'desktop.log').read_text(encoding='utf-8-sig')
                assert log.count(' child=False initializing') == 2
                assert (data / 'server.log').read_text(encoding='utf-8').count(' ready: http://') == 2
                assert 'open-request-deferred' in log
                checks.append({'queuedReopens': 3, 'oneFreshBackendAndHost': True, 'oldWindowStaysHidden': True})
                user.PostMessageW(fresh_hwnd, 0x10, 0, 0)
                assert kernel.WaitForSingleObject(fresh_native, 15000) == 0
                assert kernel.WaitForSingleObject(fresh_backend, 5000) == 0
                # Fail before native attachment: the launcher must cancel its
                # own backend, including when Python is an exe redirector.
                icon = app / 'static/ezread.ico'; missing = icon.with_suffix('.disabled')
                icon.rename(missing)
                failing = launch()
                assert failing.wait(timeout=35) == 1
                wait(lambda: request('/api/health') is None, 8)
                checks.append({'nativeStartupFailureCancelsCreatedBackend': True})
                missing.rename(icon)
                profile = data / 'webview2-profile'
                saved_profile = data / 'webview2-profile-saved'
                assert profile.resolve().is_relative_to((ROOT / 'work').resolve())
                assert saved_profile.resolve().is_relative_to((ROOT / 'work').resolve())
                profile.rename(saved_profile)
                profile.write_text('Block WebView initialization after backend health capture.', encoding='utf-8')
                # Reusing a separately running backend must never transfer
                # ownership to this failed startup attempt.
                with (data / 'existing-server.log').open('w') as server_log:
                    existing = subprocess.Popen([sys.executable, str(app / 'server.py')], env=env, cwd=app,
                                                stdout=server_log, stderr=server_log, creationflags=subprocess.CREATE_NO_WINDOW)
                    launchers.append(existing)
                    alive = wait(lambda: request('/api/health'))
                    existing_handle = retain(alive['pid'])
                    assert launch().wait(timeout=35) == 1
                    assert request('/api/health')['instance_id'] == alive['instance_id']
                    checks.append({'nativeStartupFailurePreservesExistingBackend': True})
                    request('/api/shutdown', {'instance_id': alive['instance_id']})
                    assert kernel.WaitForSingleObject(existing_handle, 10000) == 0
                profile.unlink()
                saved_profile.rename(profile)
                # Kill an uncommitted launcher after its backend starts. The
                # backend must notice owner death even across a venv redirector.
                owner_code = ('import os,sys,time; from pathlib import Path; '
                              'from ezread.launching import StartupServer; '
                              'owner=StartupServer(); log=open("owner-server.log","w"); '
                              'owner.start(Path(sys.executable),Path.cwd(),log); '
                              'print(os.getpid(),flush=True); time.sleep(60)')
                owner_process = subprocess.Popen([str(args.python.resolve()), '-c', owner_code], env=env, cwd=app,
                                                  stdout=subprocess.PIPE, stderr=output, text=True,
                                                  creationflags=subprocess.CREATE_NO_WINDOW)
                launchers.append(owner_process)
                owner_handle = retain(int(owner_process.stdout.readline()))
                owner_health = wait(lambda: request('/api/health'))
                orphan_candidate = retain(owner_health['pid'])
                kernel.TerminateProcess(owner_handle, 1)
                assert kernel.WaitForSingleObject(orphan_candidate, 8000) == 0, 'Launcher crash left an uncommitted backend.'
                checks.append({'launcherCrashCancelsUncommittedBackend': True})
            except BaseException:
                evidence = ROOT / 'work/launch-verification-failure'
                evidence.mkdir(exist_ok=True)
                output.flush()
                for path in data.glob('*.log'): shutil.copy2(path, evidence / path.name)
                raise
            finally:
                for handle in handles:
                    if kernel.WaitForSingleObject(handle, 0) != 0: kernel.TerminateProcess(handle, 1)
                    kernel.CloseHandle(handle)
                for process in launchers:
                    if process.poll() is None: process.terminate(); process.wait(timeout=5)
    report = {'checks': checks, 'realLibraryWrites': 0, 'realModelCalls': 0}
    (ROOT / 'work/launch-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__': main()
