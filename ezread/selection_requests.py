"""Per-window cancellation, including cancel-before-start and stale responses."""
import re
import threading
import time

from codex_bridge import TranslationError


def identity(data, required=False):
    client, request = data.get('client_id'), data.get('request_id')
    if client is None and request is None and not required:
        return None
    if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', value)
           for value in (client, request)):
        raise ValueError('翻译请求标识无效。')
    return client, request


class SelectionRequests:
    def __init__(self, ttl=180, limit=512):
        self.lock = threading.Lock()
        self.active = {}
        self.ended = {}
        self.progress = {}
        self.ttl, self.limit = ttl, limit

    def _prune(self):
        cutoff = time.monotonic() - self.ttl
        self.ended = {key: at for key, at in self.ended.items() if at > cutoff}
        while len(self.ended) > self.limit:
            self.ended.pop(next(iter(self.ended)))
        self.progress = {key: value for key, value in self.progress.items()
                         if key in self.ended or self.active.get(key[1], ())[:2] == (key[0], key[2])}

    def begin(self, pid, data):
        key = identity(data)
        event = threading.Event()
        if key is None:
            return event
        client, request = key
        with self.lock:
            self._prune()
            if (pid, client, request) in self.ended:
                raise TranslationError('此翻译请求已取消。', retryable=False, code='cancelled')
            previous = self.active.get(client)
            if previous:
                if previous[:2] == (pid, request):
                    raise ValueError('此翻译请求正在处理，请勿重复提交。')
                previous[2].set()
                self.ended[(previous[0], client, previous[1])] = time.monotonic()
            elif len(self.active) >= self.limit:
                raise ValueError('翻译请求过多，请稍后重试。')
            self.active[client] = pid, request, event
            self.progress[(pid, client, request)] = {'started': time.monotonic(), 'events': [], 'output': '', 'done': False}
        return event

    def report(self, pid, data, value):
        key = identity(data)
        if key is None:
            return
        with self.lock:
            record = self.progress.get((pid, *key))
            if record is None or record['done']:
                return
            if value.get('kind') == 'output':
                record['output'] = value.get('text', '')[:12000]
                return
            text = value.get('text', '')
            if not text:
                return
            events = record['events']
            # Avoid duplicate stderr/protocol reports of the same failure.
            if events and events[-1]['text'] == text:
                return
            events[:] = [{'text': text, 'kind': value.get('kind', 'status'),
                          'elapsed_ms': round((time.monotonic() - record['started']) * 1000)}]

    def snapshot(self, pid, data):
        key = identity(data, required=True)
        with self.lock:
            self._prune()
            record = self.progress.get((pid, *key))
            if record is None:
                return {'events': [], 'output': '', 'done': (pid, *key) in self.ended}
            return {'events': [dict(value) for value in record['events']],
                    'output': record['output'], 'done': record['done']}

    def finish(self, pid, data, event):
        key = identity(data)
        if key is None:
            return
        client, request = key
        with self.lock:
            current = self.active.get(client)
            if current == (pid, request, event):
                self.active.pop(client)
            self.ended[(pid, client, request)] = time.monotonic()
            record = self.progress.get((pid, client, request))
            if record:
                record['done'] = True
            self._prune()

    def cancel(self, pid, data):
        client, request = identity(data, required=True)
        with self.lock:
            self._prune()
            self.ended[(pid, client, request)] = time.monotonic()
            current = self.active.get(client)
            if current and current[:2] == (pid, request):
                current[2].set()
            self._prune()
        return {'cancelled': True}

    def shutdown(self):
        with self.lock:
            for _, _, event in self.active.values():
                event.set()


def ensure_current(event):
    if event.is_set():
        raise TranslationError('此翻译请求已取消。', retryable=False, code='cancelled')
