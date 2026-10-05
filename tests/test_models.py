"""Offline model discovery tests: no login, credentials, or inference required."""
import copy
import json
import queue
import threading
import time
import unittest
from unittest.mock import patch

import codex_models as models


def entry(slug='test-model', *, default=True, efforts=('low', 'high'), default_effort='low'):
    return {'id': 'catalog-' + slug, 'model': slug, 'displayName': 'Test model',
            'defaultReasoningEffort': default_effort,
            'supportedReasoningEfforts': [{'reasoningEffort': effort, 'description': 'mock'} for effort in efforts],
            'isDefault': default, 'hidden': False, 'privatePayload': 'must not escape'}


class FakeProcess:
    """Minimal line transport driven by actual writes from _query."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.lines = queue.Queue()
        self.stdin = self
        self.stdout = self
        self.stderr = None
        self.returncode = None

    def write(self, line):
        request = json.loads(line)
        self.requests.append(request)
        if 'id' in request:
            reply = self.replies.pop(0)
            self.lines.put(json.dumps({'id': request['id'], **reply}) + '\n')

    def flush(self):
        pass

    def readline(self, limit):
        return self.lines.get(timeout=2)

    def close(self):
        self.returncode = 0
        self.lines.put('')

    def wait(self, timeout):
        self.returncode = 0
        return 0


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.original_cache = copy.deepcopy(models._cache)
        self.original_state = (models._busy, models._closed, models._last_attempt, models._last_success)
        models._cache.update(status='unavailable', models=[], default_model_id=None,
                             refreshing=False, stale=False, updated_at=None, checked_at=None,
                             error='not loaded', error_code='not_loaded')
        models._busy = models._closed = False
        models._last_attempt = models._last_success = 0.0

    def tearDown(self):
        self.assertFalse(models._busy, 'test leaked a background refresh')
        models._cache.clear()
        models._cache.update(self.original_cache)
        models._busy, models._closed, models._last_attempt, models._last_success = self.original_state

    def install(self, entries=None):
        models._cache.update(models.normalize({'data': entries or [entry()]}), status='ready', error='', error_code=None)
        models._last_attempt = models._last_success = time.monotonic()

    def wait_refresh(self):
        deadline = time.monotonic() + 2
        while models._busy and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertFalse(models._busy)

    def test_normalize_allowlist_and_model_slug(self):
        hidden = dict(entry('hidden'), hidden=True)
        raw = {'data': [entry(), hidden, {'model': '--invalid'}, entry()]}
        result = models.normalize(raw)
        self.assertEqual(len(result['models']), 1)
        result_entry = result['models'][0]
        self.assertEqual(result_entry['id'], 'test-model')
        self.assertEqual(result_entry['catalog_id'], 'catalog-test-model')
        self.assertEqual(result_entry['supported_reasoning_efforts'], ['low', 'high'])
        self.assertNotIn('privatePayload', result_entry)
        self.assertEqual(result['default_model_id'], 'test-model')

    def test_missing_or_invalid_defaults_are_not_invented(self):
        value = entry(default=False, default_effort='invented')
        self.install([value])
        self.assertIsNone(models.snapshot()['default_model_id'])
        with self.assertRaisesRegex(models.ModelError, '默认模型'):
            models.resolve_config()
        with self.assertRaisesRegex(models.ModelError, '默认推理强度'):
            models.resolve_config('test-model')
        self.assertEqual(models.resolve_config('test-model', 'high')['reasoning_effort'], 'high')

    def test_resolve_defaults_and_reject_invalid_combinations(self):
        self.install()
        self.assertEqual(models.resolve_config(), {'model': 'test-model', 'reasoning_effort': 'low'})
        for args, code in [(('missing', 'low'), 'unsupported_model'), (('test-model', 'max'), 'unsupported_effort'), (([], 'low'), 'invalid_config')]:
            with self.assertRaises(models.ModelError) as caught:
                models.resolve_config(*args)
            self.assertEqual(caught.exception.code, code)
        with patch.object(models, '_query', side_effect=AssertionError('no RPC during resolve')):
            self.assertEqual(models.validate_config('test-model', 'high')['reasoning_effort'], 'high')

    def test_unavailable_does_not_fabricate_default(self):
        with self.assertRaises(models.ModelError) as caught:
            models.resolve_config()
        self.assertEqual(caught.exception.code, 'unavailable')

    def test_actual_transport_initializes_and_paginates_only_model_list(self):
        fake = FakeProcess([{'result': {}},
                            {'result': {'data': [entry()], 'nextCursor': 'page-two'}},
                            {'result': {'data': [entry('second', default=False)], 'nextCursor': None}}])
        with patch.object(models.codex_bridge, '_cli', return_value='official-codex'), \
             patch.object(models.subprocess, 'Popen', return_value=fake) as popen:
            result = models._query()
        self.assertEqual([r['method'] for r in fake.requests], ['initialize', 'initialized', 'model/list', 'model/list'])
        self.assertEqual(fake.requests[-1]['params']['cursor'], 'page-two')
        self.assertEqual(len(result['models']), 2)
        command = popen.call_args.args[0]
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertEqual(popen.call_args.kwargs['stderr'], models.subprocess.DEVNULL)
        self.assertNotIn(fake, models._processes)

    def test_empty_intermediate_page_and_cursor_loop(self):
        pages = iter([{'data': [], 'nextCursor': 'continue'}, {'data': [entry()], 'nextCursor': None}])
        self.assertEqual(models._fetch_pages(lambda *_: next(pages))['default_model_id'], 'test-model')
        with self.assertRaises(models.ModelError) as caught:
            models._fetch_pages(lambda *_: {'data': [entry()], 'nextCursor': 'repeat'})
        self.assertEqual(caught.exception.code, 'invalid_response')

    def test_rpc_errors_redacted(self):
        secret = 'sensitive-diagnostic-not-for-clients'
        fake = FakeProcess([{'result': {}}, {'error': {'code': -32601, 'message': secret}}])
        with patch.object(models.codex_bridge, '_cli', return_value='official-codex'), \
             patch.object(models.subprocess, 'Popen', return_value=fake):
            with self.assertRaises(models.ModelError) as caught:
                models._query()
        self.assertEqual(caught.exception.code, 'unsupported')
        self.assertNotIn(secret, str(caught.exception))

    def test_get_does_not_wait_and_refresh_is_single_flight(self):
        started, release = threading.Event(), threading.Event()
        def slow_query():
            started.set()
            release.wait(2)
            return models.normalize({'data': [entry()]})
        with patch.object(models, '_query', side_effect=slow_query) as query:
            try:
                before = time.monotonic()
                state = models.get()
                self.assertLess(time.monotonic() - before, .5)
                self.assertTrue(started.wait(.5))
                self.assertEqual(state['status'], 'loading')
                self.assertTrue(state['refreshing'])
                models.get(refresh=True)
                models.snapshot(force=True)
                self.assertEqual(query.call_count, 1)
            finally:
                release.set()
                self.wait_refresh()
        self.assertEqual(models.snapshot()['status'], 'ready')

    def test_refresh_failure_preserves_old_directory_and_timestamp(self):
        self.install()
        models._cache['updated_at'] = 'previous-success'
        models._last_attempt -= 4
        with patch.object(models, '_query', side_effect=models.ModelError('network unavailable', 'network')):
            models.get(refresh=True)
            self.wait_refresh()
        state = models.snapshot()
        self.assertEqual(state['status'], 'stale')
        self.assertTrue(state['stale'])
        self.assertEqual(state['updated_at'], 'previous-success')
        self.assertEqual(state['default_model_id'], 'test-model')
        self.assertEqual(state['error_code'], 'network')
        self.assertEqual(models.resolve_config()['model'], 'test-model')

    def test_failure_without_cache_is_unavailable_and_throttled(self):
        with patch.object(models, '_query', side_effect=models.ModelError('network unavailable', 'network')) as query:
            models.get()
            self.wait_refresh()
            models.get()
            self.assertEqual(query.call_count, 1)
        self.assertEqual(models.snapshot()['status'], 'unavailable')
        self.assertEqual(models.snapshot()['models'], [])

    def test_snapshot_is_detached_and_ttl_stale(self):
        self.install()
        state = models.snapshot()
        state['models'][0]['supported_reasoning_efforts'].append('fabricated')
        self.assertNotIn('fabricated', models.snapshot()['models'][0]['supported_reasoning_efforts'])
        models._last_success -= models.TTL + 1
        self.assertTrue(models.snapshot()['stale'])

    def test_shutdown_prevents_new_refresh(self):
        models.shutdown()
        with patch.object(models, '_query', side_effect=AssertionError('closed service')):
            self.assertEqual(models.get()['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
