"""Cancel work and preserve recoverable state before application shutdown."""
import secrets
import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import threading

_watch_lock = threading.Lock()
_watching = set()


def validate_instance(app, value):
    if not isinstance(value, str) or not value.isascii() or not secrets.compare_digest(value, app.INSTANCE_ID):
        raise ValueError('后台实例已变化，未关闭其他实例。')


def pause_jobs(app):
    with app.LOCK:
        app.RESUME_REQUESTED.clear()
        for event in app.CANCEL.values():
            event.set()
        app.SELECTION_REQUESTS.shutdown()
        for doc in app.all_docs():
            if not (doc.get('translation', {}).get('status') in ('running', 'queued') or
                    any(doc.get(name + '_status') in ('running', 'queued') for name in ('summarize', 'team')) or
                    doc.get('reading_structure', {}).get('status') in ('running', 'queued')):
                continue
            def pause(current):
                if current.get('translation', {}).get('status') in ('running', 'queued'):
                    current['translation'].update(status='paused', error='')
                for name in ('summarize', 'team'):
                    if current.get(name + '_status') in ('running', 'queued'):
                        current.update({name + '_status': 'paused', name + '_error': ''})
                if current.get('reading_structure', {}).get('status') in ('running', 'queued'):
                    current['reading_structure'].update(status='needs_review', error='应用已关闭，结构校对已停止。')
            app.update_doc(doc['id'], pause)


def prepare_shutdown(app, data):
    if set(data) != {'instance_id'}:
        raise ValueError('后台实例已变化，未关闭其他实例。')
    validate_instance(app, data.get('instance_id'))
    app.SHUTDOWN.set()
    pause_jobs(app)
    return {'closing': True, 'instance_id': app.INSTANCE_ID}


def attach_desktop(app, httpd, data):
    """Hold an OS handle to the verified native host; its death stops this backend."""
    if set(data) != {'instance_id', 'process_id'} or type(data.get('process_id')) is not int or data['process_id'] <= 0 or os.name != 'nt':
        raise ValueError('桌面窗口标识无效。')
    validate_instance(app, data.get('instance_id'))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000 | 0x1000, False, data['process_id'])
    if not handle:
        raise ValueError('桌面窗口已退出。')
    path = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(path))
    expected = (app.ROOT / 'desktop/bin/EzRead.Desktop.exe').resolve()
    if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)) or Path(path.value).resolve() != expected:
        kernel.CloseHandle(handle)
        raise ValueError('不是当前 EzRead 的桌面窗口，未绑定其他进程。')
    key = app.INSTANCE_ID, data['process_id']
    with _watch_lock:
        if key in _watching:
            kernel.CloseHandle(handle)
            return {'attached': True}
        _watching.add(key)
    def monitor():
        try:
            if kernel.WaitForSingleObject(handle, 0xffffffff) == 0 and not app.SHUTDOWN.is_set():
                prepare_shutdown(app, {'instance_id': app.INSTANCE_ID})
                httpd.shutdown()
        finally:
            kernel.CloseHandle(handle)
            with _watch_lock:
                _watching.discard(key)
    threading.Thread(target=monitor, daemon=True, name='ezread-desktop-lifetime').start()
    return {'attached': True}
