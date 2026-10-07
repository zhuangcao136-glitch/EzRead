"""Dedicated transport protocol/lifecycle checks with a fake local CLI."""
import json
import io
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
        return self.lines.get()
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
        self.turn_count = 0
        self.turn_ids = {}
        self.auto_complete = True
        self.prelude = False
        self.streaming = False
        self.finish_on_final = True
        self.hold_thread = False
        self.hold_turn = False
        self.pending_thread = self.pending_turn = None
        self.thread_started = threading.Event()
        self.turn_started = threading.Event()
    def emit(self, message):
        self.stdout.lines.put(json.dumps(message) + '\n')
    def write(self, line):
        request = json.loads(line)
        self.requests.append(request)
        method, params = request.get('method'), request.get('params', {})
        if method == 'thread/start':
            self.count += 1
            self.pending_thread = request['id'], f'thread-{self.count}'
            self.thread_started.set()
            if not self.hold_thread: self.release_thread()
        elif method == 'turn/start':
            tid = params['threadId']
            answer = '预热完成' if params['outputSchema']['properties']['translation'].get('enum') == ['预热完成'] else '模拟句子译文'
            self.turn_count += 1
            turn_id = self.turn_ids[tid] = f'turn-{self.turn_count}'
            self.pending_turn = request['id'], tid, turn_id
            self.turn_started.set()
            if self.hold_turn: return
            self.release_turn()
            if self.auto_complete:
                if self.streaming:
                    self.emit({'method': 'error', 'params': {'threadId': tid, 'turnId': 'obsolete-turn',
                        'willRetry': True, 'error': {'message': 'obsolete retry message'}}})
                    self.emit({'method': 'error', 'params': {'threadId': tid, 'turnId': turn_id,
                        'willRetry': True, 'error': {'message': 'Network is not available. Reconnecting... 1/5'}}})
                    self.emit({'method': 'item/reasoning/textDelta', 'params': {'threadId': tid, 'delta': 'private thinking'}})
                    self.emit({'method': 'item/started', 'params': {'threadId': tid, 'turnId': turn_id,
                        'item': {'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer'}}})
                    for delta in ('{"translation":"模拟', '句子译文"}'):
                        self.emit({'method': 'item/agentMessage/delta', 'params': {'threadId': tid, 'turnId': turn_id, 'itemId': 'answer', 'delta': delta}})
                if self.prelude:
                    self.emit({'method': 'item/completed', 'params': {'threadId': tid, 'turnId': turn_id,
                        'item': {'type': 'agentMessage', 'phase': 'commentary', 'text': '{"translation":"interim"}'}}})
                    self.emit({'method': 'item/completed', 'params': {'threadId': tid, 'turnId': 'wrong-turn',
                        'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': '{"translation":"foreign"}'}}})
                self.emit({'method': 'item/completed', 'params': {'threadId': tid, 'turnId': turn_id,
                    'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': json.dumps({'translation': answer}, ensure_ascii=False)}}})
                if self.finish_on_final: self.complete(tid, turn_id)
        elif method == 'turn/interrupt':
            self.emit({'id': request['id'], 'result': {}})
            self.complete(params['threadId'], params['turnId'], status='interrupted')
        elif 'id' in request:
            self.emit({'id': request['id'], 'result': {}})
    def release_thread(self):
        number, tid = self.pending_thread
        self.emit({'id': number, 'result': {'thread': {'id': tid}}})
    def release_turn(self):
        number, tid, turn_id = self.pending_turn
        self.emit({'method': 'turn/started', 'params': {'threadId': tid, 'turn': {'id': turn_id}}})
        self.emit({'id': number, 'result': {'turn': {'id': turn_id}}})
    def complete(self, tid, turn_id, status='completed'):
        self.emit({'method': 'turn/completed', 'params': {'threadId': tid, 'turn': {'id': turn_id, 'status': status, 'items': []}}})
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
        kwargs.setdefault('paper_id', 'a' * 16)
        return channel.translate('Synthetic source.', model='chosen-model', reasoning_effort='high', **kwargs)
    def test_preparation_sends_one_fixed_test_turn_and_translations_reuse_the_warmed_thread(self):
        channel.prepare(model='warm-model', reasoning_effort='low')
        channel.prepare(model='other-model')
        self.assertEqual(self.process.count, 1)
        self.assertEqual(self.process.turn_count, 1)
        warm = next(r for r in self.process.requests if r.get('method') == 'turn/start')
        self.assertEqual(warm['params']['model'], 'warm-model')
        self.assertEqual(warm['params']['effort'], 'low')
        self.assertNotIn('UNTRUSTED_PAPER_DATA', warm['params']['input'][0]['text'])
        self.assertIn('connection warm-up test', warm['params']['input'][0]['text'])
        self.assertEqual(self.translate(), '模拟句子译文')
        self.assertEqual(self.process.count, 1)
        self.assertEqual(self.process.turn_count, 2)
        turn = [r for r in self.process.requests if r.get('method') == 'turn/start'][-1]
        self.assertEqual(turn['params']['model'], 'chosen-model')
        self.assertEqual(turn['params']['threadId'], warm['params']['threadId'])
    def test_warmup_is_ready_only_after_a_successful_completed_turn(self):
        self.process.finish_on_final = False
        result = []
        worker = threading.Thread(target=lambda: (channel.prepare(), result.append(True)))
        worker.start()
        self.assertTrue(self.process.turn_started.wait(1))
        time.sleep(.08)
        self.assertTrue(worker.is_alive())
        self.assertFalse(channel._transport.warmed)
        self.assertEqual(result, [])
        self.process.complete('thread-1', 'turn-1'); worker.join(1)
        self.assertEqual(result, [True])
        self.assertTrue(channel._transport.warmed)
    def test_warmup_keeps_retrying_until_the_model_finishes_and_failure_never_marks_it_ready(self):
        self.process.auto_complete = False
        result = []
        def run():
            try: channel.prepare()
            except codex_bridge.TranslationError as error: result.append(error.code)
        worker = threading.Thread(target=run); worker.start()
        self.assertTrue(self.process.turn_started.wait(1))
        self.process.emit({'method': 'error', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1',
            'willRetry': True, 'error': {'message': 'Network is not available. Reconnecting... 1/5'}}})
        time.sleep(.08)
        self.assertTrue(worker.is_alive())
        self.assertFalse(any(r.get('method') == 'turn/interrupt' for r in self.process.requests))
        self.process.complete('thread-1', 'turn-1', status='failed'); worker.join(1)
        self.assertEqual(result, ['cli'])
        self.assertFalse(channel._transport.warmed)
        self.process.auto_complete = True
        self.assertEqual(self.translate(), '模拟句子译文')
        self.assertEqual(self.process.count, 1)
    def test_closing_during_the_background_test_interrupts_its_turn_and_releases_the_process(self):
        self.process.auto_complete = False
        event, result = threading.Event(), []
        def run():
            try: channel.prepare(cancel_event=event)
            except codex_bridge.TranslationError as error: result.append(error.code)
        worker = threading.Thread(target=run); worker.start()
        self.assertTrue(self.process.turn_started.wait(1))
        event.set(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ['cancelled'])
        self.assertFalse(channel._transport.warmed)
        self.assertTrue(any(r.get('method') == 'turn/interrupt' for r in self.process.requests))
        channel.shutdown()
        self.assertEqual(self.process.poll(), 0)
    def test_request_can_cancel_while_background_preparation_continues_and_reuses_the_late_thread(self):
        self.process.hold_thread = True
        prepared, progress, result = [], [], []
        worker = threading.Thread(target=lambda: (channel.prepare(), prepared.append(True)))
        worker.start()
        self.assertTrue(self.process.thread_started.wait(1))
        channel._transport.report_diagnostic('Network is not available. Reconnecting... 1/5')
        event = threading.Event()
        def run():
            try: self.translate(cancel_event=event, on_progress=progress.append)
            except codex_bridge.TranslationError as error: result.append(error.code)
        request = threading.Thread(target=run); request.start()
        for _ in range(20):
            if progress: break
            time.sleep(.01)
        self.assertTrue(any('等待翻译连接准备完成' in value['text'] for value in progress))
        self.assertFalse(any('网络不可用' in value['text'] or '预热完成' in value['text'] for value in progress))
        event.set(); request.join(1)
        self.assertEqual(result, ['cancelled'])
        self.assertTrue(worker.is_alive())
        self.process.release_thread(); worker.join(1)
        self.assertEqual(prepared, [True])
        self.assertEqual(self.translate(paper_id='b' * 16), '模拟句子译文')
        self.assertEqual(self.process.count, 1)
    def test_application_exit_cancels_in_progress_preparation(self):
        self.process.hold_thread = True
        event, result = threading.Event(), []
        def run():
            try: channel.prepare(cancel_event=event)
            except codex_bridge.TranslationError as error: result.append(error.code)
        worker = threading.Thread(target=run); worker.start()
        self.assertTrue(self.process.thread_started.wait(1))
        event.set(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ['cancelled'])
        channel.shutdown()
        self.assertEqual(self.process.poll(), 0)
    def test_first_selection_creates_one_thread_and_reuses_it_until_application_exit(self):
        channel.subprocess.Popen.assert_not_called()
        start = time.monotonic()
        self.assertEqual(self.translate(), '模拟句子译文')
        self.assertEqual(self.translate(target_language='en'), '模拟句子译文')
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(channel.subprocess.Popen.call_count, 1)
        starts = [r for r in self.process.requests if r.get('method') == 'thread/start']
        self.assertEqual(len(starts), 1)
        self.assertTrue(all(r['params']['ephemeral'] for r in starts))
        self.assertTrue(all(r['params']['sandbox'] == 'read-only' for r in starts))
        turns = [r for r in self.process.requests if r.get('method') == 'turn/start']
        self.assertEqual(len({r['params']['threadId'] for r in turns}), 1)
        self.assertFalse(any(r.get('method') == 'thread/unsubscribe' for r in self.process.requests))
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
        self.assertEqual(interrupted['params'], {'threadId': 'thread-1', 'turnId': 'turn-1'})
        self.process.auto_complete = True
        self.assertEqual(self.translate(paper_id='b' * 16), '模拟句子译文')
        self.assertEqual(self.process.count, 1)
    def test_switching_papers_keeps_one_shared_conversation_and_identifies_each_current_selection(self):
        self.translate()
        self.translate(paper_id='b' * 16)
        self.translate()
        turns = [r['params'] for r in self.process.requests if r.get('method') == 'turn/start']
        self.assertEqual([r['threadId'] for r in turns], ['thread-1', 'thread-1', 'thread-1'])
        self.assertEqual(self.process.count, 1)
        self.assertEqual(channel.subprocess.Popen.call_count, 1)
        for turn, paper_id in zip(turns, ['a' * 16, 'b' * 16, 'a' * 16]):
            prompt = turn['input'][0]['text']
            data = json.loads(prompt.split('UNTRUSTED_PAPER_DATA (JSON):\n', 1)[1])
            self.assertEqual(data, {'paper_id': paper_id, 'selected_text': 'Synthetic source.'})
            self.assertIn('Use terminology context only from selections with the same paper_id.', prompt)
    def test_application_shutdown_releases_context_and_next_launch_creates_a_new_one(self):
        self.translate()
        previous = channel._transport
        channel.shutdown()
        self.assertTrue(previous.closed)
        self.assertIsNone(channel._transport)
        self.assertEqual(self.process.poll(), 0)
        current = FakeProcess()
        channel.subprocess.Popen.return_value = current
        self.assertEqual(self.translate(paper_id='b' * 16), '模拟句子译文')
        self.assertIsNot(channel._transport, previous)
        self.assertEqual(current.count, 1)
        self.assertEqual(channel.subprocess.Popen.call_count, 2)
    def test_final_translation_returns_immediately_but_next_turn_waits_for_previous_completion(self):
        self.process.finish_on_final = False
        self.assertEqual(self.translate(), '模拟句子译文')
        result = []
        worker = threading.Thread(target=lambda: result.append(self.translate()))
        worker.start(); time.sleep(.08)
        self.assertTrue(worker.is_alive())
        self.assertEqual(self.process.turn_count, 1)
        self.process.finish_on_final = True
        self.process.complete('thread-1', 'turn-1')
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ['模拟句子译文'])
        self.assertEqual(self.process.count, 1)
    def test_cancellation_before_turn_ack_interrupts_later_and_keeps_context(self):
        self.process.hold_turn = True
        event = threading.Event(); result = []
        def run():
            try: self.translate(cancel_event=event)
            except codex_bridge.TranslationError as error: result.append(error.code)
        worker = threading.Thread(target=run); worker.start()
        self.assertTrue(self.process.turn_started.wait(1))
        event.set(); worker.join(1)
        self.assertEqual(result, ['cancelled'])
        self.assertFalse(channel._transport.closed)
        self.process.release_turn()
        self.process.hold_turn = False
        self.assertEqual(self.translate(), '模拟句子译文')
        self.assertEqual(self.process.count, 1)
        self.assertTrue(any(r.get('method') == 'turn/interrupt' for r in self.process.requests))
    def test_cancelled_thread_creation_is_retained_after_its_delayed_ack(self):
        self.process.hold_thread = True
        event = threading.Event(); result = []
        def run():
            try: self.translate(cancel_event=event)
            except codex_bridge.TranslationError as error: result.append(error.code)
        worker = threading.Thread(target=run); worker.start()
        self.assertTrue(self.process.thread_started.wait(1))
        event.set(); worker.join(1)
        self.assertEqual(result, ['cancelled'])
        self.process.release_thread()
        self.process.hold_thread = False
        self.assertEqual(self.translate(), '模拟句子译文')
        self.assertEqual(self.process.count, 1)
    def test_waiting_has_no_default_deadline_and_only_user_cancellation_stops_it(self):
        self.process.auto_complete = False
        event = threading.Event(); result = []
        def run():
            try: self.translate(cancel_event=event)
            except codex_bridge.TranslationError as error: result.append(error.code)
        with patch.object(time, 'monotonic', return_value=10**12):
            worker = threading.Thread(target=run); worker.start()
            self.assertTrue(self.process.turn_started.wait(1))
            time.sleep(.08)
            self.assertTrue(worker.is_alive())
            self.assertFalse(any(r.get('method') == 'turn/interrupt' for r in self.process.requests))
            event.set(); worker.join(1)
        self.assertEqual(result, ['cancelled'])
    def test_pre_cancelled_request_never_starts_a_process(self):
        event = threading.Event(); event.set()
        with self.assertRaises(codex_bridge.TranslationError) as caught:
            self.translate(cancel_event=event)
        self.assertEqual(caught.exception.code, 'cancelled')
        channel.subprocess.Popen.assert_not_called()
    def test_invalid_model_json_is_rejected(self):
        for value in ('bad', '{"translation":""}', '{"translation":1}', '{"translation":"ok","extra":true}'):
            with self.subTest(value=value), self.assertRaises(codex_bridge.TranslationError):
                channel.parse_translation(value)
    def test_interim_messages_and_other_turns_cannot_replace_final_translation(self):
        self.process.prelude = True
        self.assertEqual(self.translate(), '模拟句子译文')
    def test_public_retry_and_streamed_answer_are_visible_without_private_reasoning(self):
        self.process.streaming = True
        events = []
        self.assertEqual(self.translate(on_progress=events.append), '模拟句子译文')
        self.assertTrue(any('网络不可用' in value['text'] and '1/5' in value['text'] for value in events))
        self.assertEqual([value['text'] for value in events if value['kind'] == 'output'], ['模拟', '模拟句子译文'])
        self.assertEqual(events[-1]['text'], '翻译完成。')
        self.assertFalse(any('private thinking' in value['text'] for value in events))
        self.assertFalse(any('obsolete retry message' in value['text'] for value in events))
    def test_stderr_output_is_visible_and_credentials_are_redacted(self):
        self.process.stderr = io.StringIO('Network is not available. Reconnecting... 2/5 access_token="secret-token"\n')
        events = []
        self.translate(on_progress=events.append)
        diagnostics = '\n'.join(value['text'] for value in events if value['kind'] == 'diagnostic')
        self.assertIn('网络不可用', diagnostics)
        self.assertIn('2/5', diagnostics)
        self.assertNotIn('secret-token', diagnostics)
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
