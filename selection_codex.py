"""Dedicated reusable Codex transport for temporary sentence translations.

No paper chat/history, tools, credentials, or user configuration are modified.
Each request gets an ephemeral thread. The complete validated final message is
returned immediately; process shutdown and account notifications never gate UI.
"""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
import tomllib

import codex_bridge as bridge
from ezread.config import VERSION

IDLE_SECONDS = 120
MAX_THREADS = 64
_pool_lock = threading.Lock()
_call_lock = threading.Lock()
_transport = None


def cancelled():
    return bridge.TranslationError('此翻译请求已取消。', retryable=False, code='cancelled')


def check(deadline, event):
    if event is not None and event.is_set():
        raise cancelled()
    if time.monotonic() >= deadline:
        raise bridge.TranslationError('句子翻译超时，请稍后重试。', code='timeout')


def isolated_overrides():
    """Inspect configuration names only; never read auth.json or emit values."""
    home = Path(bridge._environment()['CODEX_HOME'])
    path = home / 'config.toml'
    try:
        config = tomllib.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError, ValueError) as exc:
        raise bridge.TranslationError('无法读取 Codex 配置，请检查本机配置文件。', code='configuration') from exc
    values = ['forced_login_method="chatgpt"', 'model_provider="openai"',
              'approval_policy="never"', 'features.shell_tool=false', 'features.unified_exec=false',
              'features.apps=false', 'features.memories=false', 'agents.enabled=false',
              'web_search="disabled"', 'project_doc_max_bytes=0']
    for section in ('mcp_servers', 'plugins'):
        for name in config.get(section, {}):
            # This CLI splits override keys on dots; quoted dotted keys do not
            # reliably target existing entries. Fail closed on ambiguous names.
            if not re.fullmatch(r'[A-Za-z0-9_@-]+', name):
                raise bridge.TranslationError('Codex 配置名称无法用于独立翻译通道。', code='configuration')
            values.append(f'{section}.{name}.enabled=false')
    skills = sorted((home / 'skills').rglob('SKILL.md'))
    disabled = ','.join('{path=' + json.dumps(str(path)) + ',enabled=false}' for path in skills)
    values.append('skills.config=[' + disabled + ']')
    return values


class Transport:
    def __init__(self, deadline, cancel_event):
        cli = bridge._cli()
        if not cli:
            raise bridge.TranslationError('未找到官方 Codex，请先安装并登录。', code='cli')
        connection = bridge.status()
        check(deadline, cancel_event)
        if not connection.get('authenticated'):
            raise bridge.TranslationError(connection['message'], retryable=False, code='auth')
        command = [cli, 'app-server', '--stdio']
        for value in isolated_overrides():
            command += ['-c', value]
        if sum(len(value) + 3 for value in command) > 28000:
            raise bridge.TranslationError('Codex 技能配置过长，无法启动独立翻译通道。', code='configuration')
        self.temp = tempfile.TemporaryDirectory(prefix='ezread-selection-')
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.responses = {}
        self.events = {}
        self.turns = {}
        self.number = 0
        self.closed = False
        self.thread_count = 0
        self.last_used = time.monotonic()
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                env=bridge._environment(), **bridge._flags())
        except OSError as exc:
            self.temp.cleanup()
            raise bridge.TranslationError('无法启动句子翻译通道。', code='cli') from exc
        self.diagnostics = bridge._StartupDiagnostics(self.process)
        self.reader = threading.Thread(target=self._read, daemon=True, name='ezread-selection-reader')
        self.reader.start()
        try:
            self.rpc('initialize', {'clientInfo': {'name': 'ezread', 'title': 'EzRead', 'version': VERSION}},
                     min(deadline, time.monotonic() + 25), cancel_event)
            self.send({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise
        threading.Thread(target=self._idle, daemon=True, name='ezread-selection-idle').start()

    def send(self, payload):
        with self.write_lock:
            if self.closed:
                raise bridge.TranslationError('句子翻译通道已关闭，请重试。', code='transport')
            try:
                self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                self._end()
                raise bridge.TranslationError('句子翻译连接中断，请重试。', code='transport') from exc

    def fire(self, method, params):
        with self.lock:
            self.number += 1
            number = self.number
        self.send({'id': number, 'method': method, 'params': params})

    def rpc(self, method, params, deadline, event):
        check(deadline, event)
        with self.lock:
            self.number += 1
            number = self.number
            inbox = queue.Queue(maxsize=1)
            self.responses[number] = inbox
        try:
            self.send({'id': number, 'method': method, 'params': params})
            while True:
                check(deadline, event)
                try:
                    response = inbox.get(timeout=.05)
                except queue.Empty:
                    continue
                if response is None:
                    raise self.connection_error()
                if 'error' in response:
                    raise bridge._classify_error(str(response['error'].get('message', '')))
                return response.get('result', {})
        finally:
            with self.lock:
                self.responses.pop(number, None)

    def connection_error(self):
        code = self.diagnostics.startup_code()
        messages = {'permissions': 'Codex 运行目录不可写，请从正常的 EzRead 入口启动。',
                    'auth': 'Codex 登录已失效，请重新登录 ChatGPT 账号。',
                    'network': '无法连接 Codex 服务，请检查网络。'}
        return bridge.TranslationError(messages.get(code, '句子翻译连接中断，请重试。'), code=code)

    def _read(self):
        try:
            while not self.closed:
                line = self.process.stdout.readline(262145)
                if not line:
                    break
                if len(line) > 262144:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                with self.lock:
                    if 'id' in message and 'method' not in message:
                        target = self.responses.get(message['id'])
                    elif 'id' in message:
                        # The translator never services tools or approval requests.
                        self.send({'id': message['id'], 'error': {'code': -32601, 'message': 'Tools are disabled.'}})
                        continue
                    else:
                        payload = message.get('params') or {}
                        tid = payload.get('threadId')
                        method = message.get('method')
                        if method == 'turn/started':
                            self.turns[tid] = (payload.get('turn') or {}).get('id')
                        if method not in {'error', 'turn/completed', 'item/completed'}:
                            continue
                        if method == 'item/completed' and (payload.get('item') or {}).get('type') != 'agentMessage':
                            continue
                        target = self.events.get(tid)
                    if target is not None:
                        try:
                            target.put_nowait(message)
                        except queue.Full:
                            break
        except (OSError, ValueError, TypeError):
            pass
        finally:
            self._end()

    def _end(self):
        with self.lock:
            self.closed = True
            for inbox in [*self.responses.values(), *self.events.values()]:
                try:
                    inbox.put_nowait(None)
                except queue.Full:
                    pass

    def translate(self, text, model, effort, language, deadline, event):
        thread_id = turn_id = None
        self.last_used = time.monotonic()
        try:
            started = self.rpc('thread/start', {'model': model, 'cwd': self.temp.name,
                'approvalPolicy': 'never', 'sandbox': 'read-only', 'ephemeral': True,
                'baseInstructions': bridge._BASE_INSTRUCTIONS,
                'config': {'features': {'shell_tool': False, 'unified_exec': False, 'apps': False,
                                        'memories': False}, 'web_search': 'disabled'}}, deadline, event)
            thread_id = started.get('thread', {}).get('id')
            if not isinstance(thread_id, str) or not thread_id:
                raise bridge.TranslationError('Codex 未创建句子翻译会话。', code='invalid_response')
            self.thread_count += 1
            inbox = queue.Queue(maxsize=16)
            with self.lock:
                self.events[thread_id] = inbox
            target = 'precise academic English' if language == 'en' else 'precise simplified Chinese'
            prompt = (f'Translate the selected academic text into {target}. Preserve terms, numbers, '
                      'symbols, units and qualifiers. Return only the requested translation, with no preface.\n'
                      'UNTRUSTED_PAPER_DATA (JSON):\n' + json.dumps({'selected_text': text}, ensure_ascii=False))
            schema = {'type': 'object', 'additionalProperties': False,
                      'properties': {'translation': {'type': 'string'}}, 'required': ['translation']}
            started = self.rpc('turn/start', {'threadId': thread_id,
                'input': [{'type': 'text', 'text': prompt, 'text_elements': []}],
                'model': model, 'effort': effort, 'outputSchema': schema}, deadline, event)
            turn_id = started.get('turn', {}).get('id')
            while True:
                check(deadline, event)
                try:
                    message = inbox.get(timeout=.05)
                except queue.Empty:
                    continue
                if message is None:
                    raise self.connection_error()
                payload = message.get('params') or {}
                if payload.get('turnId') and turn_id and payload['turnId'] != turn_id:
                    continue
                method = message.get('method')
                if method == 'error':
                    if payload.get('willRetry'):
                        continue
                    raise bridge._classify_error(str((payload.get('error') or {}).get('message', '')))
                if method == 'item/completed':
                    item = payload.get('item') or {}
                    if item.get('phase') in (None, 'final_answer'):
                        result = parse_translation(item.get('text'))
                        check(deadline, event)
                        return result
                if method == 'turn/completed':
                    turn = payload.get('turn') or {}
                    if turn_id and turn.get('id') != turn_id:
                        continue
                    if turn.get('status') != 'completed':
                        raise bridge._classify_error(str((turn.get('error') or {}).get('message', '')))
                    candidates = [item for item in turn.get('items', []) if item.get('type') == 'agentMessage'
                                  and item.get('phase') in (None, 'final_answer')]
                    if not candidates:
                        raise bridge.TranslationError('句子翻译未返回完整译文。', code='invalid_response')
                    result = parse_translation(candidates[-1].get('text'))
                    check(deadline, event)
                    return result
        except bridge.TranslationError as exc:
            if exc.code in {'cancelled', 'timeout'}:
                tid = turn_id or self.turns.get(thread_id)
                if thread_id and tid and not self.closed:
                    try:
                        self.fire('turn/interrupt', {'threadId': thread_id, 'turnId': tid})
                    except bridge.TranslationError:
                        self.close()
                else:
                    self.close()  # Initialization/turn start cancelled before its ID arrived.
            raise
        finally:
            self.last_used = time.monotonic()
            if thread_id:
                with self.lock:
                    self.events.pop(thread_id, None)
                    self.turns.pop(thread_id, None)
                if not self.closed:
                    try:
                        self.fire('thread/unsubscribe', {'threadId': thread_id})
                    except bridge.TranslationError:
                        pass

    def _idle(self):
        while not self.closed:
            time.sleep(5)
            with _pool_lock, self.lock:
                expired = (not _call_lock.locked() and not self.events and not self.responses and
                           time.monotonic() - self.last_used > IDLE_SECONDS)
                if expired:
                    self.close()

    def close(self):
        self._end()
        bridge._stop(self.process)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe is None:
                continue
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        self.temp.cleanup()


def parse_translation(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise bridge.TranslationError('句子译文格式不完整，请重试。', code='invalid_response') from exc
    if (not isinstance(value, dict) or set(value) != {'translation'} or
            not isinstance(value['translation'], str) or not 1 <= len(value['translation'].strip()) <= 12000):
        raise bridge.TranslationError('句子译文格式无效，请重试。', code='invalid_response')
    return value['translation'].strip()


def translate(text, *, model, reasoning_effort, target_language='zh', cancel_event=None, timeout=120):
    global _transport
    if target_language not in {'zh', 'en'}:
        raise ValueError('Unsupported translation language.')
    deadline = time.monotonic() + timeout
    while not _call_lock.acquire(timeout=.05):
        check(deadline, cancel_event)
    try:
        check(deadline, cancel_event)
        with _pool_lock:
            if _transport is None or _transport.closed or _transport.thread_count >= MAX_THREADS:
                if _transport is not None:
                    _transport.close()
                _transport = Transport(deadline, cancel_event)
            transport = _transport
        return transport.translate(text, model, reasoning_effort, target_language, deadline, cancel_event)
    finally:
        _call_lock.release()


def shutdown():
    global _transport
    with _pool_lock:
        transport, _transport = _transport, None
    if transport is not None:
        transport.close()


atexit.register(shutdown)
