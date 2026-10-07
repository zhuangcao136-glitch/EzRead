"""Dedicated reusable Codex transport for temporary sentence translations.

No paper chat/history, tools, credentials, or user configuration are modified.
All selections share one in-memory thread until application exit. The validated final message is
returned immediately; process shutdown and account notifications never gate UI.
"""
from __future__ import annotations

import atexit
import json
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 uses the compatible standalone parser.
    import tomli as tomllib

import codex_bridge as bridge
from ezread.config import VERSION
from ezread.selection_output import diagnostic, partial_translation

_pool_lock = threading.Lock()
_call_lock = threading.Lock()
_preparation_lock = threading.Lock()
_preparing = False
_transport = None


class Conversation:
    def __init__(self, key):
        self.key = key
        self.thread_id = None
        self.turn_id = None
        self.error = None
        self.created = threading.Event()
        self.ready = threading.Event()
        self.ready.set()
        self.cancel_pending = False
        self.interrupt_sent = False


def cancelled():
    return bridge.TranslationError('此翻译请求已取消。', retryable=False, code='cancelled')


def check(event):
    if event is not None and event.is_set():
        raise cancelled()


def emit(callback, text, kind='status'):
    if callback is not None:
        callback({'kind': kind, 'text': text})


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
    def __init__(self, cancel_event, on_progress=None):
        self.on_progress = on_progress
        emit(on_progress, '正在检查 Codex 登录状态…')
        cli = bridge._cli()
        if not cli:
            raise bridge.TranslationError('未找到官方 Codex，请先安装并登录。', code='cli')
        connection = bridge.status()
        check(cancel_event)
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
        self.messages = {}
        self.conversations = {}
        self.by_thread = {}
        self.pending_threads = {}
        self.pending_turns = {}
        self.number = 0
        self.closed = False
        self.warmed = False
        try:
            emit(on_progress, '正在启动独立翻译通道…')
            self.process = bridge.spawn(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                env=bridge._environment(), **bridge._flags())
        except OSError as exc:
            self.temp.cleanup()
            raise bridge.TranslationError('无法启动句子翻译通道。', code='cli') from exc
        self.diagnostics = bridge._StartupDiagnostics(self.process, self.report_diagnostic)
        self.reader = threading.Thread(target=self._read, daemon=True, name='ezread-selection-reader')
        self.reader.start()
        try:
            emit(on_progress, '正在初始化翻译连接…')
            self.rpc('initialize', {'clientInfo': {'name': 'ezread', 'title': 'EzRead', 'version': VERSION}},
                     cancel_event)
            self.send({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def publish(self, text, kind='status'):
        with self.lock:
            callback = self.on_progress
        emit(callback, text, kind)

    def report_diagnostic(self, raw):
        text = diagnostic(raw)
        if text:
            self.publish(text, 'diagnostic')

    def report_notification(self, method, payload):
        """Public model output and failures only; private reasoning is excluded."""
        tid = payload.get('threadId')
        if tid not in self.events:
            return
        turn_id = payload.get('turnId') or (payload.get('turn') or {}).get('id')
        if turn_id and self.turns.get(tid) and turn_id != self.turns[tid]:
            return
        if method == 'error':
            error = payload.get('error') or {}
            text = diagnostic(error.get('message', ''))
            details = diagnostic(error.get('additionalDetails', ''))
            if details and details not in text:
                text += '；' + details
            if payload.get('willRetry'):
                text += '（通道将自动重试）'
            if text:
                self.publish(text, 'diagnostic')
        elif method == 'item/started':
            item = payload.get('item') or {}
            if item.get('type') == 'agentMessage':
                self.messages[(tid, item.get('id'))] = {'phase': item.get('phase'), 'raw': ''}
                if item.get('phase') in (None, 'final_answer'):
                    self.publish('模型开始返回译文…')
        elif method == 'item/agentMessage/delta':
            value = self.messages.get((tid, payload.get('itemId')))
            delta = payload.get('delta')
            if value is not None and isinstance(delta, str) and value['phase'] in (None, 'final_answer'):
                value['raw'] = (value['raw'] + delta)[:24000]
                text = partial_translation(value['raw'])
                if text:
                    self.publish(text, 'output')
        elif method == 'item/completed':
            item = payload.get('item') or {}
            if item.get('type') == 'agentMessage' and item.get('phase') == 'commentary':
                self.report_diagnostic(item.get('text', ''))
        elif method == 'turn/completed':
            turn = payload.get('turn') or {}
            if turn.get('status') in ('failed', 'interrupted'):
                error = turn.get('error') or {}
                self.report_diagnostic(error.get('message', ''))
                self.report_diagnostic(error.get('additionalDetails', ''))

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

    def rpc(self, method, params, event, conversation=None):
        check(event)
        with self.lock:
            self.number += 1
            number = self.number
            inbox = queue.Queue(maxsize=1)
            self.responses[number] = inbox
            if conversation is not None:
                if method == 'thread/start':
                    self.pending_threads[number] = conversation
                elif method == 'turn/start':
                    conversation.ready.clear()
                    conversation.turn_id = None
                    conversation.cancel_pending = False
                    conversation.interrupt_sent = False
                    self.turns.pop(conversation.thread_id, None)
                    self.pending_turns[number] = conversation
        try:
            self.send({'id': number, 'method': method, 'params': params})
            while True:
                check(event)
                try:
                    response = inbox.get(timeout=.05)
                except queue.Empty:
                    continue
                if response is None:
                    raise self.connection_error()
                if 'error' in response:
                    self.report_diagnostic(response['error'].get('message', ''))
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
                reply = interrupt = None
                with self.lock:
                    if 'id' in message and 'method' not in message:
                        created = self.pending_threads.pop(message['id'], None)
                        if created is not None:
                            tid = ((message.get('result') or {}).get('thread') or {}).get('id')
                            if isinstance(tid, str) and tid:
                                created.thread_id = tid
                                self.by_thread[tid] = created
                            else:
                                created.error = bridge._classify_error(str((message.get('error') or {}).get('message', '')))
                                self.conversations.pop(created.key, None)
                            created.created.set()
                        started = self.pending_turns.pop(message['id'], None)
                        if started is not None:
                            turn = (message.get('result') or {}).get('turn') or {}
                            if isinstance(turn.get('id'), str):
                                started.turn_id = turn['id']
                                self.turns[started.thread_id] = turn['id']
                                if turn.get('status') in ('completed', 'failed', 'interrupted'):
                                    started.ready.set()
                            else:
                                started.ready.set()
                            interrupt = self.take_interrupt(started)
                        target = self.responses.get(message['id'])
                    elif 'id' in message:
                        # The translator never services tools or approval requests.
                        reply = {'id': message['id'], 'error': {'code': -32601, 'message': 'Tools are disabled.'}}
                        target = None
                    else:
                        payload = message.get('params') or {}
                        tid = payload.get('threadId')
                        method = message.get('method')
                        if method == 'turn/started':
                            self.turns[tid] = (payload.get('turn') or {}).get('id')
                            session = self.by_thread.get(tid)
                            if session is not None:
                                session.turn_id = self.turns[tid]
                                session.ready.clear()
                                interrupt = self.take_interrupt(session)
                            if tid in self.events:
                                self.publish('翻译通道已接收本轮请求，等待模型输出…')
                        elif method == 'turn/completed':
                            session = self.by_thread.get(tid)
                            if session is not None and session.turn_id == (payload.get('turn') or {}).get('id'):
                                session.ready.set()
                        self.report_notification(method, payload)
                        target = self.events.get(tid) if method in {'error', 'turn/completed', 'item/completed'} else None
                        if method == 'item/completed' and (payload.get('item') or {}).get('type') != 'agentMessage': target = None
                    if target is not None:
                        try:
                            target.put_nowait(message)
                        except queue.Full:
                            break
                if reply is not None:
                    self.send(reply)
                if interrupt is not None:
                    self.fire('turn/interrupt', interrupt)
        except (OSError, ValueError, TypeError):
            pass
        finally:
            self._end()

    def _end(self):
        with self.lock:
            self.closed = True
            for session in self.conversations.values():
                session.created.set()
                session.ready.set()
            for inbox in [*self.responses.values(), *self.events.values()]:
                try:
                    inbox.put_nowait(None)
                except queue.Full:
                    pass

    def take_interrupt(self, session):
        if session.cancel_pending and session.turn_id and not session.interrupt_sent and not session.ready.is_set():
            session.interrupt_sent = True
            return {'threadId': session.thread_id, 'turnId': session.turn_id}
        return None

    def wait(self, signal, event):
        while not signal.wait(.05):
            check(event)
            if self.closed: raise self.connection_error()
        check(event)
        if self.closed: raise self.connection_error()

    def conversation(self, model, event):
        key = '_selection'
        with self.lock:
            session = self.conversations.get(key)
            new = session is None
            if new:
                session = self.conversations[key] = Conversation(key)
        try:
            if new:
                self.publish('正在创建本次软件运行共用的翻译对话…')
                self.rpc('thread/start', {'model': model, 'cwd': self.temp.name,
                'approvalPolicy': 'never', 'sandbox': 'read-only', 'ephemeral': True,
                'baseInstructions': bridge._BASE_INSTRUCTIONS,
                'config': {'features': {'shell_tool': False, 'unified_exec': False, 'apps': False,
                                        'memories': False}, 'web_search': 'disabled'}}, event, conversation=session)
            else:
                self.publish('继续使用本次软件运行的翻译对话…')
            self.wait(session.created, event)
            if session.error is not None: raise session.error
            if not isinstance(session.thread_id, str) or not session.thread_id:
                raise bridge.TranslationError('Codex 未创建句子翻译会话。', code='invalid_response')
            return session
        except bridge.TranslationError:
            with self.lock:
                if session.thread_id is None and session not in self.pending_threads.values():
                    self.conversations.pop(session.key, None)
            raise

    def prepare(self, model, event, on_progress=None):
        with self.lock:
            self.on_progress = on_progress
        try:
            self.conversation(model, event)
            self.publish('划线翻译对话已建立。')
        finally:
            with self.lock:
                self.on_progress = None

    def translate(self, text, model, effort, language, event, on_progress=None, paper_id=None, warmup=False):
        thread_id = turn_id = None
        session = None
        requested_turn = False
        with self.lock:
            self.on_progress = on_progress
        try:
            session = self.conversation(model, event)
            thread_id = session.thread_id
            if not session.ready.is_set(): self.publish('等待上一轮结束，保留翻译对话…')
            self.wait(session.ready, event)
            inbox = queue.Queue(maxsize=16)
            with self.lock:
                self.events[thread_id] = inbox
            target = 'precise academic English' if language == 'en' else 'precise simplified Chinese'
            prompt = (f'Translate the selected academic text into {target}. Preserve terms, numbers, '
                      'symbols, units and qualifiers. Return only the requested translation, with no preface.\n'
                      'Translate only selected_text in this request. Earlier selections may be from different papers. '
                      'Use terminology context only from selections with the same paper_id. '
                      'Do not assume selections from different papers describe the same study or translate earlier selections again. '
                      'Any public commentary must be in simplified Chinese.\n'
                      'UNTRUSTED_PAPER_DATA (JSON):\n' + json.dumps({'paper_id': paper_id, 'selected_text': text}, ensure_ascii=False))
            if warmup:
                prompt = ('This is a connection warm-up test, not paper data or translation context. '
                          'Reply only with {"translation":"预热完成"}. Do not explain, use tools, or translate any previous text.')
            schema = {'type': 'object', 'additionalProperties': False,
                      'properties': {'translation': {'type': 'string'}}, 'required': ['translation']}
            if warmup:
                schema['properties']['translation']['enum'] = ['预热完成']
            effort_label = {'low': '低', 'medium': '中', 'high': '高', 'xhigh': '很高', 'max': '最高'}.get(effort, effort)
            self.publish('正在后台发送连接测试…' if warmup else f'正在向模型发送请求（{model}，推理强度 {effort_label}）…')
            requested_turn = True
            started = self.rpc('turn/start', {'threadId': thread_id,
                'input': [{'type': 'text', 'text': prompt, 'text_elements': []}],
                'model': model, 'effort': effort, 'outputSchema': schema}, event, conversation=session)
            turn_id = started.get('turn', {}).get('id')
            if not isinstance(turn_id, str) or not turn_id:
                raise bridge.TranslationError('翻译通道未创建本轮请求。', code='invalid_response')
            final_result = None
            while True:
                check(event)
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
                        check(event)
                        if warmup:
                            final_result = result
                            continue
                        self.publish('翻译完成。')
                        return result
                if method == 'turn/completed':
                    turn = payload.get('turn') or {}
                    if turn_id and turn.get('id') != turn_id:
                        continue
                    if turn.get('status') != 'completed':
                        raise bridge._classify_error(str((turn.get('error') or {}).get('message', '')))
                    candidates = [item for item in turn.get('items', []) if item.get('type') == 'agentMessage'
                                  and item.get('phase') in (None, 'final_answer')]
                    if final_result is None and not candidates:
                        raise bridge.TranslationError('句子翻译未返回完整译文。', code='invalid_response')
                    result = final_result or parse_translation(candidates[-1].get('text'))
                    check(event)
                    if warmup and result != '预热完成':
                        raise bridge.TranslationError('后台连接测试未返回预期回复。', code='invalid_response')
                    self.publish('后台连接测试已完成。' if warmup else '翻译完成。')
                    return result
        except bridge.TranslationError as exc:
            if session is not None and session.thread_id is None:
                with self.lock:
                    if session not in self.pending_threads.values():
                        self.conversations.pop(session.key, None)
            if exc.code == 'cancelled' and requested_turn and session is not None:
                with self.lock:
                    session.cancel_pending = True
                    interrupt = self.take_interrupt(session)
                if interrupt is not None:
                    self.fire('turn/interrupt', interrupt)
            raise
        finally:
            if thread_id:
                with self.lock:
                    self.events.pop(thread_id, None)
                    self.messages = {key: value for key, value in self.messages.items() if key[0] != thread_id}
            with self.lock:
                self.on_progress = None

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


def acquire(cancel_event, on_progress):
    previous = None
    while not _call_lock.acquire(timeout=.05):
        check(cancel_event)
        with _preparation_lock:
            preparing = _preparing
        value = {'kind': 'status', 'text': '正在等待翻译连接准备完成…' if preparing else '正在等待前一个划线翻译请求结束…'}
        if value != previous:
            emit(on_progress, value['text'], value['kind'])
            previous = value


def transport(cancel_event, on_progress):
    global _transport
    check(cancel_event)
    with _pool_lock:
        if _transport is None or _transport.closed:
            if _transport is not None:
                _transport.close()
            _transport = Transport(cancel_event, on_progress)
        return _transport


def prepare(*, model=None, reasoning_effort=None, cancel_event=None, on_progress=None):
    global _preparing
    acquire(cancel_event, on_progress)
    try:
        with _preparation_lock:
            _preparing = True
        channel = transport(cancel_event, on_progress)
        channel.prepare(model, cancel_event, on_progress)
        if not channel.warmed:
            channel.translate('', model, reasoning_effort, 'zh', cancel_event, on_progress, warmup=True)
            channel.warmed = True
    finally:
        with _preparation_lock:
            _preparing = False
        _call_lock.release()


def translate(text, *, model, reasoning_effort, target_language='zh', cancel_event=None, on_progress=None, paper_id=None):
    if target_language not in {'zh', 'en'}:
        raise ValueError('Unsupported translation language.')
    if paper_id is not None and (not isinstance(paper_id, str) or not re.fullmatch(r'[a-f0-9]{16}', paper_id)):
        raise ValueError('Invalid paper identity.')
    acquire(cancel_event, on_progress)
    try:
        return transport(cancel_event, on_progress).translate(text, model, reasoning_effort, target_language, cancel_event, on_progress, paper_id)
    finally:
        _call_lock.release()


def shutdown():
    global _transport
    with _pool_lock:
        transport, _transport = _transport, None
    if transport is not None:
        transport.close()


atexit.register(shutdown)
