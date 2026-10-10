"""Windows startup ownership, independent of Python's optional exe redirector."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import uuid


def _kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, arguments in {
        'CreateMutexW': [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR],
        'CreateEventW': [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR],
        'OpenEventW': [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR],
        'OpenProcess': [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
    }.items():
        function = getattr(kernel, name)
        function.argtypes = arguments
        function.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
    for name in ('CloseHandle', 'ReleaseMutex', 'SetEvent'):
        getattr(kernel, name).argtypes = [wintypes.HANDLE]
    return kernel


@contextmanager
def startup_lock(root, directory, url):
    """Serialize preflight, backend creation and native readiness together."""
    identity = '|'.join((str(Path(root).resolve()).upper(), str(Path(directory).resolve()).upper(), url.rstrip('/')))
    name = 'Local\\EzRead.Launch.' + hashlib.sha256(identity.encode()).hexdigest()
    kernel = _kernel()
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    owned = False
    try:
        result = kernel.WaitForSingleObject(handle, 45000)
        owned = result in (0, 0x80)  # Abandoned owners do not leave a stale lock.
        if not owned:
            raise RuntimeError('EzRead 正在启动或完成上次关闭，请稍后重试。')
        yield
    finally:
        if owned:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


class StartupServer:
    """A cancel/commit lease for exactly the server this launcher creates.

    The server watches the launcher's stable OS handle until native readiness.
    This also covers launcher crashes and venv python.exe redirector children.
    """
    def __init__(self):
        self.kernel = _kernel()
        self.token = uuid.uuid4().hex
        self.process = None
        self.handles = []
        try:
            for suffix in ('Cancel', 'Commit'):
                handle = self.kernel.CreateEventW(None, True, False, self.name(self.token, suffix))
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handles.append(handle)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def name(token, suffix):
        return 'Local\\EzRead.Startup.' + token + '.' + suffix

    def start(self, executable, root, log):
        self.process = subprocess.Popen(
            [str(executable), str(root / 'launch.pyw'), '--server', self.token, str(os.getpid())],
            cwd=root, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def commit(self):
        if not self.kernel.SetEvent(self.handles[1]):
            raise ctypes.WinError(ctypes.get_last_error())
        self.close()

    def cancel(self):
        try:
            if self.handles:
                self.kernel.SetEvent(self.handles[0])
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # This is the retained Popen handle, never a health-response PID.
                    self.process.kill()
                    self.process.wait(timeout=3)
        finally:
            self.close()

    def close(self):
        for handle in self.handles:
            self.kernel.CloseHandle(handle)
        self.handles.clear()


def run_guarded_server(root, token, owner_pid):
    """The uncommitted backend can only terminate itself and its own job."""
    if uuid.UUID(token).hex != token:
        raise ValueError('Invalid startup lease.')
    from ezread.processes import attach_job
    attach_job()
    kernel = _kernel()
    handles = []
    try:
        for suffix in ('Cancel', 'Commit'):
            handle = kernel.OpenEventW(0x100000, False, StartupServer.name(token, suffix))
            if not handle:
                raise RuntimeError('Startup owner has exited.')
            handles.append(handle)
        owner = kernel.OpenProcess(0x100000, False, int(owner_pid))
        if not owner:
            raise RuntimeError('Startup owner has exited.')
        handles.append(owner)
    except BaseException:
        for handle in handles:
            kernel.CloseHandle(handle)
        raise

    def watch():
        try:
            result = kernel.WaitForMultipleObjects(3, (wintypes.HANDLE * 3)(*handles), False, 0xffffffff)
            if result != 1:  # Only the explicit commit transfers ownership to the desktop.
                os._exit(1)
        finally:
            for handle in handles:
                kernel.CloseHandle(handle)
    threading.Thread(target=watch, daemon=True, name='ezread-startup-owner').start()
    sys.argv = [str(root / 'server.py')]
    runpy.run_path(sys.argv[0], run_name='__main__')
