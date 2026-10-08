"""Offline HTTP streaming and native dispatch checks with an isolated library."""
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import paper_ai
import server

PID='0123456789abcdef'
TID='00000000-0000-0000-0000-000000000001'


class PaperChatHTTPTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        data=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='chat-http-',dir=ROOT/'work')))
        self.stack.enter_context(patch.object(server,'DATA',data))
        self.stack.enter_context(patch.object(server,'LIBRARY',data/'library'))
        self.stack.enter_context(patch.object(server,'codex_status',return_value={'authenticated':True}))
        server.init_db()
        server.put_doc({'id':PID,'hash':'chat-test','title':'Synthetic paper','blocks':[]})
        with server.db() as con:
            paper_ai.ensure(con,PID)
            con.execute('UPDATE paper_ai_generations SET main_thread_id=? WHERE paper_id=?',(TID,PID))
        self.httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.stack.enter_context(patch.object(server,'PORT',self.httpd.server_port))
        self.thread=threading.Thread(target=self.httpd.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=3)

    def request(self,action,data,origin=None):
        headers={'Content-Type':'application/json'}
        if origin: headers['Origin']=origin
        # Origin rejection happens before body parsing. Avoid an unread POST body
        # causing Windows to reset the TCP connection as the HTTP/1.0 handler exits.
        req=urllib.request.Request(f'http://127.0.0.1:{self.httpd.server_port}/api/papers/{PID}/ai/{action}',
                                   data=b'' if origin else json.dumps(data).encode(),headers=headers,method='POST')
        return urllib.request.urlopen(req,timeout=3)

    def test_first_event_arrives_before_answer_completes(self):
        gate=threading.Event()
        def ask(*_,on_progress,**kwargs):
            on_progress({'type':'status','label':'等待 Codex 响应'})
            if not gate.wait(2): raise RuntimeError('stream buffered')
            on_progress({'type':'reasoning','text':'checking evidence'})
            return {'answer':'verified answer','citations':[],'thread_id':TID}
        with patch.object(paper_ai,'ask',side_effect=ask):
            try:
                with self.request('chat-stream',{'question':'实验结果？'}) as response:
                    self.assertIn('application/x-ndjson',response.headers['Content-Type'])
                    self.assertEqual(json.loads(response.readline())['type'],'status')
                    gate.set()
                    rest=[json.loads(line) for line in response]
                    self.assertEqual([e['type'] for e in rest],['reasoning','completed'])
            finally: gate.set()

    def test_stream_errors_are_terminal_events_and_invalid_request_is_http_error(self):
        with patch.object(paper_ai,'ask',side_effect=paper_ai.SessionError('连接失败')):
            with self.request('chat-stream',{'question':'结果？'}) as response:
                self.assertEqual(json.loads(response.readline())['type'],'error')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('chat-stream',{'question':''})
        self.assertEqual(caught.exception.code,400)

    def test_open_resolves_saved_thread_and_rejects_arbitrary_url_or_cross_origin(self):
        with patch.object(paper_ai,'open_in_codex',return_value={'ok':True}) as native:
            with self.request('open',{'generation':1}) as response:
                self.assertTrue(json.load(response)['ok'])
            native.assert_called_once_with(TID)
            for data,origin in [({'url':'codex://unrelated'},None),({'generation':1},'https://unrelated.example')]:
                with self.assertRaises(urllib.error.HTTPError): self.request('open',data,origin)
            native.assert_called_once()

    def test_duplicate_stream_is_rejected_without_waiting_on_paper_lock(self):
        with paper_ai.paper_lock(PID):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request('chat-stream',{'question':'duplicate'})
            self.assertEqual(caught.exception.code,409)


@unittest.skipUnless(os.name=='nt','Windows protocol dispatch')
class NativeChatTests(unittest.TestCase):
    def test_dispatch_and_error_feedback(self):
        with patch.object(paper_ai.os,'startfile') as native:
            self.assertTrue(paper_ai.open_in_codex(TID)['ok'])
            native.assert_called_once_with(f'codex://threads/{TID}')
            for invalid in [None,'codex://threads/x','../outside',TID+'?url=https://example.com']:
                with self.assertRaises(ValueError): paper_ai.open_in_codex(invalid)
        with patch.object(paper_ai.os,'startfile',side_effect=OSError('unregistered protocol')):
            with self.assertRaisesRegex(ValueError,'无法打开 Codex'): paper_ai.open_in_codex(TID)
