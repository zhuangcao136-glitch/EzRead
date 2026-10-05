"""File actions use only active library PDFs, with native actions mocked."""
import base64
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desktop_files
import server


class FileActionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.data = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='file-actions-', dir=ROOT / 'work')))
        self.stack.enter_context(patch.object(server, 'DATA', self.data))
        self.stack.enter_context(patch.object(server, 'LIBRARY', self.data / 'library'))
        self.copy = self.stack.enter_context(patch.object(desktop_files, 'copy_pdf'))
        self.reveal = self.stack.enter_context(patch.object(desktop_files, 'reveal_pdf'))
        server.init_db()
        self.pid = '0123456789abcdef'
        self.doc = {'id': self.pid, 'hash': 'file-actions', 'title': 'Paper', 'filename': '触觉 paper $().pdf'}
        server.put_doc(self.doc)
        self.source = self.data / 'library' / self.pid / 'original.pdf'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'%PDF-1.7\nfile-action-test')
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.stack.enter_context(patch.object(server, 'PORT', self.httpd.server_port))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def request(self, action, payload=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        request = urllib.request.Request(f'http://127.0.0.1:{self.httpd.server_port}/api/papers/{self.pid}/{action}',
                                         data=json.dumps(payload or {}).encode(), headers=headers, method='POST')
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    def test_copy_preserves_filename_bytes_and_original_metadata(self):
        result = self.request('copy-pdf', {'path': 'C:/unrelated.pdf'})
        self.assertEqual(result, {'ok': True, 'filename': self.doc['filename']})
        target = self.copy.call_args.args[0]
        self.assertEqual(target.name, self.doc['filename'])
        self.assertTrue(target.is_relative_to(self.data / 'clipboard'))
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        self.assertEqual(server.get_doc(self.pid), self.doc)
        self.reveal.assert_not_called()

    def test_reveal_resolves_original_pdf_without_creating_clipboard_cache(self):
        self.assertEqual(self.request('reveal-pdf'), {'ok': True})
        self.reveal.assert_called_once_with(self.source.resolve())
        self.copy.assert_not_called()
        self.assertFalse((self.data / 'clipboard').exists())

    def test_sanitize_filename_and_reserved_windows_names(self):
        for filename in ['../danger:"file?.pdf', 'CON.pdf', 'LPT2.pdf', 'x' * 200, '']:
            server.put_doc({**self.doc, 'filename': filename})
            result = self.request('copy-pdf')
            target = self.copy.call_args.args[0]
            self.assertTrue(target.is_relative_to(self.data / 'clipboard' / self.pid))
            self.assertEqual(target.suffix, '.pdf')
            self.assertNotRegex(result['filename'], r'[<>:"/\\|?*]')
            self.assertNotRegex(result['filename'], r'^(CON|LPT2)\.')

    def test_missing_deleted_and_invalid_ids_cannot_trigger_native_action(self):
        self.source.unlink()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('copy-pdf')
        self.assertEqual(error.exception.code, 400)
        server.put_doc({**self.doc, 'deleted': True})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('reveal-pdf')
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(ValueError):
            server.paper_file_action('../outside', 'copy-pdf')
        self.copy.assert_not_called()
        self.reveal.assert_not_called()

    def test_cross_origin_rejected_before_native_action(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('copy-pdf', origin='https://unrelated.example')
        self.assertEqual(error.exception.code, 403)
        self.copy.assert_not_called()

    def test_clipboard_failure_is_an_error_not_a_success(self):
        self.copy.side_effect = ValueError('剪贴板不可用，请重试。')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('copy-pdf')
        self.assertEqual(error.exception.code, 400)
        self.assertIn('剪贴板不可用', json.load(error.exception)['error'])


@unittest.skipUnless(sys.platform == 'win32', 'Windows desktop integration')
class NativeFileCommandTests(unittest.TestCase):
    def test_copy_uses_static_sta_script_and_environment_path(self):
        with tempfile.TemporaryDirectory(prefix='native-file-', dir=ROOT / 'work') as folder:
            path = Path(folder) / '中文 $().pdf'
            path.write_bytes(b'%PDF-1.7')
            with patch.object(desktop_files.subprocess, 'run') as run:
                desktop_files.copy_pdf(path)
            args, options = run.call_args.args[0], run.call_args.kwargs
            self.assertIn('-Sta', args)
            self.assertEqual(base64.b64decode(args[-1]).decode('utf-16-le'), desktop_files.COPY_SCRIPT)
            self.assertEqual(options['env']['EZREAD_CLIPBOARD_PDF'], str(path.resolve()))
            self.assertEqual(options['creationflags'], subprocess.CREATE_NO_WINDOW)
            self.assertNotIn(str(path), args)
            self.assertTrue(options['check'])

    def test_reveal_uses_argument_list_without_shell(self):
        with tempfile.TemporaryDirectory(prefix='native-file-', dir=ROOT / 'work') as folder:
            path = Path(folder) / '中文, paper.pdf'
            path.write_bytes(b'%PDF-1.7')
            with patch.object(desktop_files.subprocess, 'Popen') as popen:
                desktop_files.reveal_pdf(path)
            args = popen.call_args.args[0]
            self.assertEqual(args[1:], ['/select,', str(path.resolve())])
            self.assertFalse(popen.call_args.kwargs.get('shell', False))

    def test_copy_timeout_is_reported(self):
        with patch.object(desktop_files.subprocess, 'run', side_effect=subprocess.TimeoutExpired('powershell', 15)):
            with self.assertRaisesRegex(ValueError, '超时'):
                desktop_files.copy_pdf(Path(__file__))


if __name__ == '__main__':
    unittest.main()
