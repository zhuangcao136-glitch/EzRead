"""Retired journal metrics stay private; covers use extracted figures."""
import json
import queue
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pdf_tools
import server


class MetricRetirementTests(unittest.TestCase):
    def test_public_paper_hides_old_metrics_but_keeps_fixed_tier(self):
        doc = {
            'id': '0123456789abcdef', 'paper_type': 'journal',
            'journal': 'Nature Communications', 'jif': 18.1,
            'quartiles': [{'quartile': 'Q1', 'year': 2025}],
            'metrics_status': 'completed', 'conference_rankings': [],
        }
        work = ROOT / 'work'
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='metric-preview-', dir=work) as folder:
            data = Path(folder)
            with patch.object(server, 'DATA', data), patch.object(server, 'LIBRARY', data / 'library'):
                server.init_db()
                for full in (False, True):
                    result = server.public_doc(doc, full)
                    self.assertIn(result['journal_tier'], ('top', 'important', 'other'))
                    for key in ('jif', 'quartiles', 'metrics_status', 'conference_rankings',
                                'tier_basis', 'tier_source_url'):
                        self.assertNotIn(key, result)

    def test_import_does_not_queue_metrics_and_old_endpoint_is_gone(self):
        work = ROOT / 'work'
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='metric-retirement-', dir=work) as folder:
            data = Path(folder)
            jobs = queue.Queue()
            with patch.object(server, 'DATA', data), patch.object(server, 'LIBRARY', data / 'library'), \
                 patch.object(server, 'JOBS', jobs), patch.object(server, 'QUEUED', set()):
                server.init_db()

                def fake_import(content, filename):
                    doc = {'id': '0123456789abcdef', 'hash': 'mock-pdf',
                           'journal': 'Nature Communications', 'paper_type': 'journal',
                           'deleted': False, 'pages': [], 'blocks': [],
                           'translation': {'status': 'idle'}}
                    server.put_doc(doc)
                    return server.public_doc(doc)

                httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f'http://127.0.0.1:{httpd.server_port}'
                    boundary = 'ezread-test-boundary'
                    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="paper.pdf"\r\n'
                            'Content-Type: application/pdf\r\n\r\n%PDF-mock\r\n'
                            f'--{boundary}--\r\n').encode()
                    req = urllib.request.Request(url + '/api/import', data=body, method='POST',
                                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
                    with patch.object(server, 'PORT', httpd.server_port), \
                         patch.object(server, 'import_pdf', side_effect=fake_import), \
                         urllib.request.urlopen(req, timeout=3) as response:
                        payload = json.load(response)
                    self.assertEqual(len(payload['papers']), 1)
                    self.assertTrue(jobs.empty())
                    # This retired route responds before reading a request body.
                    # An unread body can reset the connection on Windows.
                    req = urllib.request.Request(url + '/api/papers/0123456789abcdef/metrics',
                                                 data=b'', method='POST',
                                                 headers={'Content-Type': 'application/json'})
                    with patch.object(server, 'PORT', httpd.server_port), \
                         self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(req, timeout=3)
                    self.assertEqual(error.exception.code, 404)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=3)

    def test_cover_crop_maps_figure_selection_to_source_page(self):
        figure = {'id': 'fig-1', 'page': 3, 'bbox': [.2, .3, .8, .7]}
        with patch.object(pdf_tools, 'cover_crop', return_value='cover-test.jpg') as crop:
            result = pdf_tools.cover_crop_figure(Path('unused'), figure, [.25, .5, .75, 1])
        self.assertEqual(result, 'cover-test.jpg')
        crop.assert_called_once()
        self.assertEqual(crop.call_args.args[:2], (Path('unused'), 3))
        for actual, expected in zip(crop.call_args.args[2], [.35, .5, .65, .7]):
            self.assertAlmostEqual(actual, expected)
        with self.assertRaises(ValueError):
            pdf_tools.cover_crop_figure(Path('unused'), figure, [0, 0, 2, 1])

    def test_cover_rejects_page_crop_without_figure(self):
        work = ROOT / 'work'
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='figure-cover-', dir=work) as folder:
            data = Path(folder)
            with patch.object(server, 'DATA', data), patch.object(server, 'LIBRARY', data / 'library'):
                server.init_db()
                server.put_doc({'id': '0123456789abcdef', 'hash': 'mock', 'paper_type': 'journal',
                                'figures': [{'id': 'fig-1', 'page': 1, 'path': 'figure-001.jpg',
                                             'bbox': [.1, .1, .9, .9]}]})
                httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f'http://127.0.0.1:{httpd.server_port}/api/papers/0123456789abcdef/cover'
                    def request(payload):
                        return urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                                      headers={'Content-Type': 'application/json'})
                    with patch.object(server, 'PORT', httpd.server_port):
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            urllib.request.urlopen(request({'page': 1, 'bbox': [0, 0, 1, 1]}), timeout=3)
                        self.assertEqual(error.exception.code, 400)
                        with urllib.request.urlopen(request({'figure_id': 'fig-1'}), timeout=3) as response:
                            payload = json.load(response)
                    self.assertEqual(payload['paper']['cover'], 'figure-001.jpg')
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
