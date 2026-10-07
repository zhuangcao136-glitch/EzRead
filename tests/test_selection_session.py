"""Nonblocking, empty conversation preparation and HTTP boundaries."""
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

import codex_bridge
import server
from ezread.selection_session import SelectionSession


class SelectionSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = SelectionSession()
        self.app = SimpleNamespace(SHUTDOWN=threading.Event(), settings=Mock(return_value={'selection_translation_model': 'chosen-model', 'selection_translation_reasoning_effort': 'low'}))

    def join(self):
        self.session.worker.join(1)
        self.assertFalse(self.session.worker.is_alive())

    def test_start_deduplicates_and_does_not_wait_for_connection_or_send_paper_text(self):
        started, release = threading.Event(), threading.Event()
        def prepare(**kwargs):
            kwargs['on_progress']({'text': '网络不可用，正在重新连接…'})
            started.set()
            release.wait(2)
        with patch.object(codex_bridge, 'prepare_selection', side_effect=prepare) as prepare_call, \
             patch.object(codex_bridge, 'translate_selection') as translate:
            self.assertEqual(self.session.start(self.app)['status'], 'preparing')
            self.assertTrue(started.wait(1))
            self.assertEqual(self.session.start(self.app)['status'], 'preparing')
            self.assertIn('重新连接', self.session.snapshot()['message'])
            release.set(); self.join()
            self.assertEqual(self.session.start(self.app)['status'], 'ready')
            prepare_call.assert_called_once()
            self.assertEqual(set(prepare_call.call_args.kwargs), {'model', 'reasoning_effort', 'cancel_event', 'on_progress'})
            self.assertEqual(prepare_call.call_args.kwargs['model'], 'chosen-model')
            self.assertEqual(prepare_call.call_args.kwargs['reasoning_effort'], 'low')
            self.assertIs(prepare_call.call_args.kwargs['cancel_event'], self.app.SHUTDOWN)
            translate.assert_not_called()

    def test_failure_does_not_retry_on_every_page_load_and_keeps_reading_available(self):
        with patch.object(codex_bridge, 'prepare_selection', side_effect=codex_bridge.TranslationError('网络不可用。', code='network')) as prepare:
            self.session.start(self.app); self.join()
            self.assertEqual(self.session.start(self.app), {'status': 'failed', 'message': '网络不可用。'})
            prepare.assert_called_once()

    def test_exit_stops_preparation_and_does_not_publish_ready_after_close(self):
        started = threading.Event()
        def prepare(**kwargs):
            started.set()
            kwargs['cancel_event'].wait(2)
        with patch.object(codex_bridge, 'prepare_selection', side_effect=prepare) as callback:
            self.session.start(self.app)
            self.assertTrue(started.wait(1))
            self.app.SHUTDOWN.set(); self.join()
            self.assertEqual(self.session.snapshot()['status'], 'stopped')
            self.session.start(self.app)
            callback.assert_called_once()

    def test_http_preparation_returns_while_worker_waits_and_rejects_text_or_cross_site_calls(self):
        started, release = threading.Event(), threading.Event()
        def prepare(**kwargs):
            started.set(); release.wait(3)
        with patch.object(server, 'SHUTDOWN', self.app.SHUTDOWN), patch.object(server, 'SELECTION_SESSION', self.session), \
             patch.object(server, 'settings', self.app.settings), patch.object(codex_bridge, 'prepare_selection', side_effect=prepare) as callback:
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            url = f'http://127.0.0.1:{httpd.server_port}/api/selection-session'
            def post(body, origin=None):
                headers = {'Content-Type': 'application/json'}
                if origin: headers['Origin'] = origin
                return opener.open(urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers), timeout=1)
            try:
                with patch.object(server, 'PORT', httpd.server_port):
                    with post({}) as response:
                        self.assertEqual(response.status, 202)
                        self.assertEqual(json.load(response)['status'], 'preparing')
                    self.assertTrue(started.wait(1))
                    with opener.open(url, timeout=1) as response:
                        self.assertEqual(json.load(response)['status'], 'preparing')
                    for body, origin, code in (({'selected_text': 'Must not be sent'}, None, 400), ({}, 'https://unrelated.example', 403)):
                        with self.assertRaises(urllib.error.HTTPError) as caught: post(body, origin)
                        self.assertEqual(caught.exception.code, code)
                    self.assertEqual(callback.call_count, 1)
            finally:
                release.set(); self.join()
                httpd.shutdown(); worker.join(1); httpd.server_close()


if __name__ == '__main__': unittest.main()
