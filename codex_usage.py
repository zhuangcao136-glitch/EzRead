"""Read account-wide Codex quota through the official app-server protocol.

No model requests, credential reads, private HTTP endpoints, or paid fallbacks.
The process owns authentication; only a small allowlist of usage fields leaves it.
"""
from __future__ import annotations

import copy
import json
import math
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone

import codex_bridge

TTL = 120
FAILURE_TTL = 30
TIMEOUT = 25
_lock = threading.RLock()
_busy = False
_last_attempt = 0.0
_last_success = 0.0
_processes = set()
_cache = {'status': 'unavailable', 'updated_at': None, 'checked_at': None,
          'refreshing': False, 'error': '尚未读取订阅用量。', 'error_code': 'not_loaded',
          'buckets': [], 'ordinary_usage_allowed': None}


class UsageError(RuntimeError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(timezone.utc).isoformat()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize(raw):
    if not isinstance(raw, dict):
        raise UsageError('订阅服务未返回可用的用量数据。', 'empty')
    many = raw.get('rateLimitsByLimitId')
    if not isinstance(many, dict) or not many:
        one = raw.get('rateLimits')
        many = {str((one or {}).get('limitId') or 'codex'): one} if isinstance(one, dict) else {}
    buckets = []
    for key, bucket in many.items():
        if not isinstance(bucket, dict):
            continue
        windows = []
        for kind in ('primary', 'secondary'):
            window = bucket.get(kind)
            if not isinstance(window, dict):
                continue
            used = window.get('usedPercent')
            if not _number(used) or used < 0:
                continue
            minutes, reset = window.get('windowDurationMins'), window.get('resetsAt')
            windows.append({'kind': kind, 'used_percent': used,
                            'remaining_percent': max(0, min(100, 100 - used)),
                            'window_minutes': minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0 else None,
                            'resets_at': reset if isinstance(reset, int) and not isinstance(reset, bool) and 0 < reset < 32503680000 else None})
        if windows:
            buckets.append({'id': str(bucket.get('limitId') or key)[:100],
                            'name': str(bucket.get('limitName') or bucket.get('limitId') or key)[:120],
                            'plan_type': bucket.get('planType') if isinstance(bucket.get('planType'), str) else None,
                            'windows': windows})
    if not buckets:
        raise UsageError('订阅服务未提供用量窗口，暂时无法显示额度。', 'empty')
    return {'buckets': buckets, 'ordinary_usage_allowed': raw.get('ordinaryUsageAllowed') if isinstance(raw.get('ordinaryUsageAllowed'), bool) else None}


def _close_process(process):
    try:
        process.stdin.close()
    except (OSError, ValueError):
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        codex_bridge._stop(process)
    for pipe in (process.stdout, process.stderr):
        if pipe:
            try:
                pipe.close()
            except (OSError, ValueError):
                pass


def _query(timeout=TIMEOUT):
    cli = codex_bridge._cli()
    if not cli:
        raise UsageError('未找到官方 Codex，请先安装并登录 ChatGPT 账号。', 'missing_cli')
    try:
        process = codex_bridge.spawn([cli, 'app-server', '--stdio', '-c', 'forced_login_method="chatgpt"'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8', errors='replace',
                                   env=codex_bridge._environment(), **codex_bridge._flags())
    except OSError as exc:
        raise UsageError('无法启动官方 Codex 用量读取进程。', 'cli') from exc
    diagnostics = codex_bridge._StartupDiagnostics(process)
    with _lock:
        _processes.add(process)
    messages = queue.Queue(maxsize=128)
    done = threading.Event()

    def read_stdout():
        try:
            while not done.is_set():
                line = process.stdout.readline(262145)
                if not line:
                    break
                if len(line) > 262144:
                    continue
                try:
                    value = json.loads(line)
                    if isinstance(value, dict) and value.get('id') in (1, 2):
                        messages.put_nowait(value)
                except (ValueError, queue.Full):
                    continue
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    reader = threading.Thread(target=read_stdout, daemon=True, name='codex-usage-reader')
    reader.start()
    deadline = time.monotonic() + timeout

    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()

    def response(request_id):
        while time.monotonic() < deadline:
            try:
                value = messages.get(timeout=min(.2, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                if done.is_set():
                    code = diagnostics.startup_code()
                    if code == 'permissions':
                        raise UsageError('Codex 运行目录不可写，请从正常的 EzRead 启动入口重新启动，或检查目录权限。', code)
                    if code == 'auth':
                        raise UsageError('无法读取订阅用量，请在官方 Codex 中重新用 ChatGPT 账号登录。', code)
                    if code == 'network':
                        raise UsageError('无法连接订阅用量服务，请检查网络后重新打开翻译设置。', code)
                    raise UsageError('Codex 进程未能返回用量，请检查官方 Codex 是否可以正常登录。', 'cli')
                continue
            if value.get('id') != request_id:
                continue
            if 'error' in value:
                error = value.get('error') or {}
                code = error.get('code') if isinstance(error, dict) else None
                message = str(error.get('message', '')).lower() if isinstance(error, dict) else ''
                if code == -32601 or 'unknown method' in message or 'method not found' in message:
                    raise UsageError('当前 Codex 版本不支持读取用量，请更新官方 Codex。', 'unsupported')
                if any(word in message for word in ('not logged', 'authentication', 'unauthorized', 'login required', 'token expired', 'refresh token', '401')):
                    raise UsageError('无法读取订阅用量，请在官方 Codex 中重新用 ChatGPT 账号登录。', 'auth')
                if any(word in message for word in ('error sending request', 'connection', 'network', 'timed out', 'dns', 'resolve host', '502', '503')):
                    raise UsageError('无法连接订阅用量服务，请检查网络后刷新。', 'network')
                raise UsageError('用量服务暂不可用，请稍后刷新。', 'service')
            return value.get('result')
        raise UsageError('读取订阅用量超时，请检查网络后刷新。', 'timeout')

    try:
        send({'method': 'initialize', 'id': 1, 'params': {'clientInfo': {'name': 'ezread', 'title': 'EzRead', 'version': '3.0.0'}}})
        response(1)
        send({'method': 'initialized', 'params': {}})
        send({'method': 'account/rateLimits/read', 'id': 2, 'params': {'excludeResetCreditDetails': True}})
        return normalize(response(2))
    except (OSError, ValueError) as exc:
        raise UsageError('无法读取 Codex 用量，请稍后刷新。', 'cli') from exc
    finally:
        done.set()
        _close_process(process)
        reader.join(timeout=1)
        with _lock:
            _processes.discard(process)
        codex_bridge._processes.release(process)


def snapshot():
    """Cached-only read, safe for frequent /api/status polling."""
    with _lock:
        value = copy.deepcopy(_cache)
        value['refreshing'] = _busy
        if value['buckets'] and (_busy or time.monotonic() - _last_success > TTL):
            value['status'] = 'stale'
        elif not value['buckets'] and _busy:
            value['status'] = 'loading'
        return value


def get(refresh=False):
    """At most one in-flight process; concurrent callers receive cached state."""
    global _busy, _last_attempt, _last_success
    with _lock:
        age = time.monotonic() - _last_attempt
        ttl = TTL if _cache['status'] == 'ready' else FAILURE_TTL
        if _busy or (_last_attempt and age < (3 if refresh else ttl)):
            return snapshot()
        _busy = True
        _last_attempt = time.monotonic()
    try:
        result = _query()
        with _lock:
            _last_success = time.monotonic()
            _cache.update(result, status='ready', error='', error_code=None,
                          updated_at=_now(), checked_at=_now())
    except UsageError as exc:
        with _lock:
            _cache.update(status='stale' if _cache['buckets'] else 'unavailable',
                          checked_at=_now(), error=str(exc), error_code=exc.code)
    except Exception:
        with _lock:
            _cache.update(status='stale' if _cache['buckets'] else 'unavailable',
                          checked_at=_now(), error='订阅用量暂不可用，请稍后刷新。', error_code='unknown')
    finally:
        with _lock:
            _busy = False
    return snapshot()


def shutdown():
    with _lock:
        processes = list(_processes)
    for process in processes:
        codex_bridge._stop(process)
