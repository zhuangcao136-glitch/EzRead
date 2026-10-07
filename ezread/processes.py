"""Lifetime of subprocesses created by this EzRead backend only."""
import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes

_lock = threading.Lock()
_processes = set()
_closing = False
_job_handle = None


class Closing(RuntimeError):
    pass


def spawn(command, **kwargs):
    with _lock:
        if _closing:
            raise Closing('EzRead is closing.')
        process = subprocess.Popen(command, **kwargs)
        _processes.add(process)
        return process


def release(process):
    with _lock:
        _processes.discard(process)


def shutdown(stop, timeout=5):
    global _closing
    with _lock:
        _closing = True
        processes = list(_processes)
    workers = [threading.Thread(target=stop, args=(process,), daemon=True) for process in processes]
    for worker in workers:
        worker.start()
    deadline = time.monotonic() + timeout
    for worker in workers:
        worker.join(max(0, deadline - time.monotonic()))


def attach_job():
    """Windows closes every remaining backend descendant when the backend exits.

    Keep the raw handle open until process exit: closing it here would also stop
    this process. The kernel closes it on normal exit or unexpected termination.
    """
    global _job_handle
    if os.name != 'nt' or _job_handle is not None:
        return
    class BasicLimit(ctypes.Structure):
        _fields_ = [('ProcessTime', ctypes.c_longlong), ('JobTime', ctypes.c_longlong),
                    ('Flags', wintypes.DWORD), ('Minimum', ctypes.c_size_t), ('Maximum', ctypes.c_size_t),
                    ('ActiveProcesses', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
                    ('Priority', wintypes.DWORD), ('Scheduling', wintypes.DWORD)]
    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]
    class ExtendedLimit(ctypes.Structure):
        _fields_ = [('Basic', BasicLimit), ('Io', IoCounters), ('ProcessMemory', ctypes.c_size_t),
                    ('JobMemory', ctypes.c_size_t), ('PeakProcessMemory', ctypes.c_size_t), ('PeakJobMemory', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimit()
    limits.Basic.Flags = 0x2000 | 0x800  # KILL_ON_JOB_CLOSE; allow explicit external-shell breakaway.
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    _job_handle = handle
