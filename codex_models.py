"""Discover translation model choices through the official Codex app server.

Only initialize and model/list are sent. Authentication stays inside the CLI;
this module never reads credentials, performs inference, or calls a paid API.
All public reads return immediately; directory refresh runs on one background
thread and preserves the last successful result when the service is unavailable.
"""
from __future__ import annotations

import copy
import json
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

import codex_bridge

TTL = 3600
FAILURE_TTL = 30
TIMEOUT = 25
MAX_PAGES = 32
_lock = threading.RLock()
_busy = False
_closed = False
_last_attempt = 0.0
_last_success = 0.0
_processes = set()
_cache = {'status': 'unavailable', 'models': [], 'default_model_id': None,
          'refreshing': False, 'stale': False, 'updated_at': None,
          'checked_at': None, 'error': '尚未读取官方模型目录。',
          'error_code': 'not_loaded'}


class ModelError(ValueError):
    def __init__(self, message, code='models'):
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(timezone.utc).isoformat()


def _identifier(value, limit=200):
    return (isinstance(value, str) and len(value) <= limit
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*', value) is not None)


def _label(value, fallback, limit=160):
    if not isinstance(value, str):
        return fallback
    return ''.join(character for character in value if character.isprintable()).strip()[:limit] or fallback


def normalize(raw):
    """Keep only validated public model metadata, never arbitrary RPC fields.

    `id` is the callable model slug for UI compatibility. `catalog_id` preserves
    the distinct protocol identifier; callers must use `model` with --model.
    Missing defaults are kept missing rather than inventing a supported value.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('data'), list):
        raise ModelError('官方模型目录格式不可用，请刷新后重试。', 'invalid_response')
    models = []
    seen = set()
    for entry in raw['data']:
        if not isinstance(entry, dict) or entry.get('hidden') is True:
            continue
        model = entry.get('model')
        if not _identifier(model) or model in seen:
            continue
        supported = []
        efforts = entry.get('supportedReasoningEfforts')
        if isinstance(efforts, list):
            for option in efforts:
                effort = option.get('reasoningEffort') if isinstance(option, dict) else None
                if _identifier(effort, 32) and effort not in supported:
                    supported.append(effort)
        default = entry.get('defaultReasoningEffort')
        models.append({'id': model, 'model': model,
                       'catalog_id': _label(entry.get('id'), model, 200),
                       'display_name': _label(entry.get('displayName'), model),
                       'supported_reasoning_efforts': supported,
                       'default_reasoning_effort': default if default in supported else None,
                       'is_default': entry.get('isDefault') is True})
        seen.add(model)
    if not models:
        raise ModelError('官方 Codex 暂未提供可选模型，请刷新目录或检查登录状态。', 'empty')
    defaults = [model['model'] for model in models if model['is_default']]
    return {'models': models,
            'default_model_id': defaults[0] if len(defaults) == 1 else None}


def _rpc_error(error):
    """Classify locally; never expose the raw server message to HTTP clients."""
    code = error.get('code') if isinstance(error, dict) else None
    message = str(error.get('message', '')).lower() if isinstance(error, dict) else ''
    if code == -32601 or any(word in message for word in ('unknown method', 'method not found')):
        return ModelError('当前 Codex 版本不支持读取模型目录，请更新官方 Codex。', 'unsupported')
    if any(word in message for word in ('not logged', 'authentication', 'unauthorized',
                                        'login required', 'token expired', 'refresh token', '401')):
        return ModelError('无法读取模型目录，请在官方 Codex 中重新用 ChatGPT 账号登录。', 'auth')
    if any(word in message for word in ('permission denied', 'access is denied', 'os error 5', 'readonly database')):
        return ModelError('Codex 无法访问自己的运行目录，请检查目录权限后刷新。', 'permissions')
    if any(word in message for word in ('error sending request', 'connection', 'network',
                                        'timed out', 'dns', 'resolve host', '502', '503')):
        return ModelError('无法连接官方模型目录，请检查网络后刷新。', 'network')
    return ModelError('官方模型目录暂不可用，请稍后刷新。', 'service')


def _fetch_pages(rpc):
    """Sequential paginated discovery; `rpc` is injectable for offline tests."""
    entries = []
    cursor = None
    seen_cursors = set()
    for _ in range(MAX_PAGES):
        params = {'includeHidden': False, 'limit': 100}
        if cursor is not None:
            params['cursor'] = cursor
        page = rpc('model/list', params)
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            raise ModelError('官方模型目录格式不可用，请刷新后重试。', 'invalid_response')
        entries.extend(page['data'])
        cursor = page.get('nextCursor')
        if cursor is None or cursor == '':
            return normalize({'data': entries})
        if not isinstance(cursor, str) or len(cursor) > 4096 or cursor in seen_cursors:
            raise ModelError('官方模型目录分页异常，请稍后刷新。', 'invalid_response')
        seen_cursors.add(cursor)
    raise ModelError('官方模型目录分页未完成，请稍后刷新。', 'invalid_response')


def _close_process(process):
    try:
        process.stdin.close()
    except (OSError, ValueError, AttributeError):
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
        raise ModelError('未找到官方 Codex，请先安装并登录 ChatGPT 账号。', 'missing_cli')
    try:
        process = subprocess.Popen(
            [cli, 'app-server', '--stdio', '-c', 'forced_login_method="chatgpt"'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
            env=codex_bridge._environment(), **codex_bridge._flags())
    except OSError as exc:
        raise ModelError('无法启动官方 Codex 模型目录读取进程。', 'cli') from exc
    diagnostics = codex_bridge._StartupDiagnostics(process)
    with _lock:
        _processes.add(process)
        already_closed = _closed
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
                    request_id = value.get('id') if isinstance(value, dict) else None
                    if isinstance(request_id, int) and not isinstance(request_id, bool) and 1 <= request_id <= MAX_PAGES + 1:
                        messages.put_nowait(value)
                except (ValueError, queue.Full):
                    continue
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    reader = threading.Thread(target=read_stdout, daemon=True, name='ezread-models-reader')
    reader.start()
    deadline = time.monotonic() + timeout
    request_id = 0

    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()

    def rpc(method, params):
        nonlocal request_id
        request_id += 1
        send({'method': method, 'id': request_id, 'params': params})
        while time.monotonic() < deadline:
            try:
                value = messages.get(timeout=min(.2, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                if done.is_set():
                    code = diagnostics.startup_code()
                    if code == 'permissions':
                        raise ModelError('Codex 运行目录不可写，请从正常的 EzRead 启动入口重新启动，或检查目录权限。', code)
                    if code == 'auth':
                        raise ModelError('无法读取模型目录，请在官方 Codex 中重新用 ChatGPT 账号登录。', code)
                    if code == 'network':
                        raise ModelError('无法连接官方模型目录，请检查网络后重新打开翻译设置。', code)
                    raise ModelError('Codex 未能返回模型目录，请检查官方 Codex 登录和运行目录权限。', 'cli')
                continue
            if value.get('id') != request_id:
                continue
            if 'error' in value:
                raise _rpc_error(value['error'])
            if 'result' not in value:
                raise ModelError('官方模型目录格式不可用，请刷新后重试。', 'invalid_response')
            return value['result']
        raise ModelError('读取官方模型目录超时，请检查网络后刷新。', 'timeout')

    try:
        if already_closed:
            raise ModelError('模型目录服务已关闭。', 'closed')
        rpc('initialize', {'clientInfo': {'name': 'ezread', 'title': 'EzRead', 'version': '3.0.0'}})
        send({'method': 'initialized', 'params': {}})
        return _fetch_pages(rpc)
    except (OSError, UnicodeError) as exc:
        raise ModelError('无法读取官方模型目录，请稍后刷新。', 'cli') from exc
    finally:
        done.set()
        _close_process(process)
        reader.join(timeout=1)
        with _lock:
            _processes.discard(process)


def snapshot(force=False):
    """Read only cached state; `force=True` schedules an asynchronous refresh."""
    if force:
        return get(refresh=True)
    with _lock:
        value = copy.deepcopy(_cache)
        value['refreshing'] = _busy
        if value['models'] and (_busy or time.monotonic() - _last_success > TTL):
            value['status'] = 'stale'
        elif not value['models'] and _busy:
            value['status'] = 'loading'
        value['stale'] = value['status'] == 'stale'
        return value


def _refresh():
    global _busy, _last_success
    try:
        result = _query()
        with _lock:
            _last_success = time.monotonic()
            _cache.update(result, status='ready', error='', error_code=None,
                          updated_at=_now(), checked_at=_now())
    except ModelError as exc:
        with _lock:
            _cache.update(status='stale' if _cache['models'] else 'unavailable',
                          checked_at=_now(), error=str(exc), error_code=exc.code)
    except Exception:
        with _lock:
            _cache.update(status='stale' if _cache['models'] else 'unavailable',
                          checked_at=_now(), error='官方模型目录暂不可用，请稍后刷新。', error_code='unknown')
    finally:
        with _lock:
            _busy = False


def get(refresh=False):
    """Start at most one background refresh and return without waiting for CLI."""
    global _busy, _last_attempt
    with _lock:
        age = time.monotonic() - _last_attempt
        ttl = TTL if _cache['status'] == 'ready' else FAILURE_TTL
        if _closed or _busy or (_last_attempt and age < (3 if refresh else ttl)):
            return snapshot()
        _busy = True
        _last_attempt = time.monotonic()
        worker = threading.Thread(target=_refresh, daemon=True, name='ezread-models-refresh')
        try:
            worker.start()
        except RuntimeError:
            _busy = False
            _cache.update(status='stale' if _cache['models'] else 'unavailable',
                          checked_at=_now(), error='无法刷新官方模型目录，请稍后重试。', error_code='thread')
        return snapshot()


def resolve_config(model='', reasoning_effort=''):
    """Resolve official defaults and validate a concrete translation snapshot.

    Uses the last successful directory, even if stale; it never waits on network
    or silently substitutes an unsupported model or reasoning level.
    """
    if model is None:
        model = ''
    if reasoning_effort is None:
        reasoning_effort = ''
    if not isinstance(model, str) or not isinstance(reasoning_effort, str):
        raise ModelError('模型和推理强度必须是有效选项。', 'invalid_config')
    model, reasoning_effort = model.strip(), reasoning_effort.strip()
    value = snapshot()
    if not value['models']:
        raise ModelError('官方模型目录尚不可用，请先刷新目录后再开始翻译。', 'unavailable')
    if not model:
        model = value['default_model_id']
        if not model:
            raise ModelError('官方目录未明确默认模型，请手动选择一个模型。', 'missing_default')
    selected = next((entry for entry in value['models'] if entry['model'] == model), None)
    if selected is None:
        raise ModelError('所选模型不在当前官方目录中，请刷新目录或重新选择。', 'unsupported_model')
    if not reasoning_effort:
        reasoning_effort = selected['default_reasoning_effort']
        if not reasoning_effort:
            raise ModelError('官方目录未明确该模型的默认推理强度，请手动选择。', 'missing_default_effort')
    if reasoning_effort not in selected['supported_reasoning_efforts']:
        raise ModelError('所选模型不支持该推理强度，请重新选择。', 'unsupported_effort')
    return {'model': model, 'reasoning_effort': reasoning_effort}


def validate_config(model='', reasoning_effort=''):
    return resolve_config(model, reasoning_effort)


def shutdown():
    global _closed
    with _lock:
        _closed = True
        processes = list(_processes)
    for process in processes:
        codex_bridge._stop(process)
