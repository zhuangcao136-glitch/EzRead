"""Dedicated transport protocol/lifecycle checks with a fake local CLI."""
import json
from pathlib import Path
import queue
import threading
import tempfile
import time
import unittest
from unittest.mock import patch

import codex_bridge
import selection_codex as channel
REAL_TEMP = tempfile.TemporaryDirectory
REAL_OVERRIDES = channel.isolated_overrides


class Pipe:
    def __init__(self):
        self.lines = queue.Queue()
    def readline(self, limit):
        return self.lines.get(timeout=3)
    def close(self):
        self.lines.put('')


class FakeProcess:
    def __init__(self):
        self.stdin = self
        self.stdout = Pipe()
        self.stderr = None
        self.returncode = None
        self.requests = []
        self.count = 0
        self.auto_complete = True
        self.prelude = False
        self.turn_started = threading.Event()
    def emit(self, message):
        self.stdout.lines.put(json.dumps(message) + '\n')
    def write(self, line):
        request = json.loads(line)
        self.requests.append(request)
        method, params = request.get('method'), request.get('params', {})
        if method == 'thread/start':
            self.count += 1
            self.emit({'id': request['id'], 'result': {'thread': {'id': f'thread-{self.count}'}}})
        elif method == 'turn/start':
            tid = params['threadId']
            self.emit({'method': 'turn/started', 'params': {'threadId': tid, 'turn': {'id': 'turn-' + tid}}})
            self.emit({'id': request['id'], 'result': {'turn': {'id': 'turn-' + tid}}})
            self.turn_started.set()
            if self.auto_complete:
                if self.prelude:
                    self.emit({'method': 'item/completed', 'params': {'threadId': tid, 'turnId': 'turn-' + tid,
                        'item': {'type': 'agentMessage', 'phase': 'commentary', 'text': '{"translation":"interim"}'}}})
                    self.emit({'method': 'item/completed', 'params': {'threadId': tid, 'turnId': 'wrong-turn',
                        'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': '{"translation":"foreign"}'}}})
                self.emit({'method': 'item/completed', 'params': {'threadId': tid, 'turnId': 'turn-' + tid,
                    'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': '{"translation":"模拟句子译文"}'}}})
                # Intentionally never send turn/completed or exit the process.
        elif 'id' in request:
            self.emit({'id': request['id'], 'result': {}})
    def flush(self): pass
    def poll(self): return self.returncode
    def close(self):
        self.returncode = 0
        self.stdout.close()


class SelectionTransportTests(unittest.TestCase):
    def setUp(self):
        channel.shutdown()
        self.process = FakeProcess()
        self.patches = [patch.object(codex_bridge, '_cli', return_value='fake-codex'),
                        patch.object(codex_bridge, 'status', return_value={'authenticated': True}),
                        patch.object(channel, 'isolated_overrides', return_value=['features.apps=false']),
                        patch.object(channel.subprocess, 'Popen', return_value=self.process),
                        patch.object(codex_bridge, '_stop', side_effect=lambda process: process.close()),
                        patch.object(channel.tempfile, 'TemporaryDirectory', side_effect=lambda **kwargs:
                                     REAL_TEMP(dir=Path(__file__).resolve().parents[1] / 'work', **kwargs))]
        for item in self.patches: item.start()
    def tearDown(self):
        channel.shutdown()
        for item in reversed(self.patches): item.stop()
    def translate(self, **kwargs):
        return channel.translate('Synthetic source.', model='chosen-model', reasoning_effort='high', **kwargs)
    def test_reuses_process_but_never_reuses_translation_thread_or_waits_for_exit(self):
        start = time.monotonic()
        self.assertEqual(self.translate(), '模拟句子译文')
        self.assertEqual(self.translate(target_language='en'), '模拟句子译文')
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(channel.subprocess.Popen.call_count, 1)
        starts = [r for r in self.process.requests if r.get('method') == 'thread/start']
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(r['params']['ephemeral'] for r in starts))
        self.assertTrue(all(r['params']['sandbox'] == 'read-only' for r in starts))
        turns = [r for r in self.process.requests if r.get('method') == 'turn/start']
        self.assertEqual(len({r['params']['threadId'] for r in turns}), 2)
        self.assertEqual(turns[0]['params']['effort'], 'high')
        self.assertEqual(turns[0]['params']['model'], 'chosen-model')
        self.assertIn('precise academic English', turns[1]['params']['input'][0]['text'])
    def test_cancel_interrupts_the_actual_model_turn(self):
        self.process.auto_complete = False
        event = threading.Event()
        result = []
        def run():
            try: self.translate(cancel_event=event)
            except codex_bridge.TranslationError as error: result.append(error.code)
        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(self.process.turn_started.wait(1))
        event.set(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ['cancelled'])
        interrupted = next(r for r in self.process.requests if r.get('method') == 'turn/interrupt')
        self.assertEqual(interrupted['params'], {'threadId': 'thread-1', 'turnId': 'turn-thread-1'})
    def test_pre_cancelled_request_never_starts_a_process(self):
        event = threading.Event(); event.set()
        with self.assertRaises(codex_bridge.TranslationError) as caught:
            self.translate(cancel_event=event)
        self.assertEqual(caught.exception.code, 'cancelled')
        channel.subprocess.Popen.assert_not_called()
    def test_timeout_interrupts_and_does_not_return_partial_output(self):
        self.process.auto_complete = False
        with self.assertRaises(codex_bridge.TranslationError) as caught:
            self.translate(timeout=.15)
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertTrue(any(r.get('method') == 'turn/interrupt' for r in self.process.requests))
    def test_invalid_model_json_is_rejected(self):
        for value in ('bad', '{"translation":""}', '{"translation":1}', '{"translation":"ok","extra":true}'):
            with self.subTest(value=value), self.assertRaises(codex_bridge.TranslationError):
                channel.parse_translation(value)
    def test_interim_messages_and_other_turns_cannot_replace_final_translation(self):
        self.process.prelude = True
        self.assertEqual(self.translate(), '模拟句子译文')
    def test_isolation_disables_user_plugins_mcp_and_memory_without_changing_config(self):
        with REAL_TEMP(dir=Path(__file__).resolve().parents[1] / 'work') as folder:
            home = Path(folder)
            path = home / 'config.toml'
            raw = '[features]\nmemories=true\n[mcp_servers.local]\ncommand="unused"\n[plugins."pdf@runtime"]\nenabled=true\n'
            path.write_text(raw, encoding='utf-8')
            with patch.object(channel, 'isolated_overrides', wraps=REAL_OVERRIDES), \
                 patch.object(codex_bridge, '_environment', return_value={'CODEX_HOME': str(home)}):
                values = channel.isolated_overrides()
            self.assertIn('mcp_servers.local.enabled=false', values)
            self.assertIn('plugins.pdf@runtime.enabled=false', values)
            self.assertIn('features.memories=false', values)
            self.assertIn('agents.enabled=false', values)
            self.assertEqual(path.read_text(encoding='utf-8'), raw)


if __name__ == '__main__':
    unittest.main()
