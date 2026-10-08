"""Editable catalogue contracts, isolated from the user's papers and model calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from ezread import journal_catalogue as catalogue
import codex_bridge
import server

ROOT = Path(__file__).resolve().parents[1]


class JournalCatalogueTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'work').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='journal-test-')
        self.data = Path(self.temp.name)
        self.before = catalogue.snapshot(self.data)
        self.row = next(r for r in self.before['rows'] if r['issn'] == '2041-1723')

    def tearDown(self):
        self.temp.cleanup()

    def change(self, tier='important'):
        return {'action': 'upsert', 'issn': self.row['issn'], 'tier': tier}

    def test_save_reclassifies_old_papers_without_writing_library(self):
        with patch.object(server, 'DATA', self.data), patch.object(server, 'LIBRARY', self.data / 'library'):
            server.init_db()
            doc = {'id': '0123456789abcdef', 'hash': 'mock-hash', 'journal': self.row['name'], 'journal_issn': self.row['issn'],
                   'paper_type': 'journal', 'blocks': [{'id': 'b1', 'translation': '保留译文', 'note': '保留笔记'}],
                   'reader_state': {'page': 2}, 'tier_needs_review': True}
            server.put_doc(doc)
            stored = server.get_doc(doc['id'])
            self.assertEqual(server.public_doc(stored)['journal_tier'], 'top')
            saved = catalogue.apply_changes(self.data, [self.change()], self.before['revision'])
            public = server.public_doc(server.get_doc(doc['id']))
            self.assertEqual(public['journal_tier'], 'important')
            self.assertNotIn('tier_needs_review', public)
            self.assertEqual(server.get_doc(doc['id']), stored)
            self.assertNotEqual(saved['revision'], self.before['revision'])
            self.assertEqual(len(list((self.data / 'journal-tier-history').glob('*.csv'))), 1)
            self.assertEqual(catalogue.classify(self.data, {**doc, 'paper_type': 'preprint'})['tier'], 'preprint')
            self.assertEqual(catalogue.classify(self.data, {**doc, 'paper_type': 'conference'})['tier'], 'conference')

    def test_other_delete_add_alias_sort_and_restart(self):
        saved = catalogue.apply_changes(self.data, [self.change('other')], self.before['revision'])
        self.assertEqual(catalogue.classify(self.data, {'journal': self.row['name']})['tier'], 'other')
        saved = catalogue.apply_changes(self.data, [{'action': 'delete', 'issn': self.row['issn']}], saved['revision'])
        self.assertNotIn(self.row['issn'], {r['issn'] for r in saved['rows']})
        row = {**self.row, 'name': 'AAA Test Journal', 'aliases': 'Test Alias', 'tier': 'important'}
        saved = catalogue.apply_changes(self.data, [{'action': 'upsert', **row}], saved['revision'])
        for tier in catalogue.TIERS:
            names = [r['name'] for r in saved['rows'] if r['tier'] == tier]
            self.assertEqual(names, sorted(names, key=str.casefold))
        catalogue._CACHE.pop(catalogue.catalogue_path(self.data).resolve(), None)
        self.assertEqual(catalogue.classify(self.data, {'journal_abbr': 'Test Alias'})['tier'], 'important')

    def test_validation_and_stale_revision_leave_valid_file_unchanged(self):
        saved = catalogue.apply_changes(self.data, [self.change()], self.before['revision'])
        path = catalogue.catalogue_path(self.data)
        raw = path.read_bytes()
        with self.assertRaises(catalogue.CatalogueConflict):
            catalogue.apply_changes(self.data, [self.change('other')], self.before['revision'])
        for changes in ([{'action': 'upsert', **self.row, 'issn': '1234-5678'}],
                        [{'action': 'upsert', 'issn': self.row['issn'], 'aliases': 'IJRR'}],
                        [{'action': 'upsert', 'issn': self.row['issn'], 'source_url': 'javascript:alert(1)'}],
                        [self.change(), self.change('other')]):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                catalogue.apply_changes(self.data, changes, saved['revision'])
        self.assertEqual(path.read_bytes(), raw)

    def test_direct_file_edits_reload_and_invalid_csv_keeps_last_valid(self):
        saved = catalogue.apply_changes(self.data, [self.change()], self.before['revision'])
        path = catalogue.catalogue_path(self.data)
        rows = saved['rows']
        next(r for r in rows if r['issn'] == self.row['issn'])['tier'] = 'other'
        path.write_bytes(catalogue._encoded(rows))
        self.assertEqual(catalogue.classify(self.data, {'journal': self.row['name']})['tier'], 'other')
        valid = catalogue.snapshot(self.data)
        path.write_text('broken csv', encoding='utf-8')
        self.assertTrue(catalogue.snapshot(self.data)['error'])
        self.assertEqual(catalogue.classify(self.data, {'journal': self.row['name']})['tier'], 'other')
        with self.assertRaises(ValueError):
            catalogue.apply_changes(self.data, [self.change()], valid['revision'])
        path.write_bytes(catalogue._encoded(rows))
        self.assertFalse(catalogue.snapshot(self.data)['error'])

    def test_cli_uses_same_live_catalogue_and_revision(self):
        changes = self.data / 'changes.json'
        changes.write_text(json.dumps({'revision': self.before['revision'], 'changes': [self.change()]}), encoding='utf-8')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/journal-catalogue.py'), '--data-dir', str(self.data),
                                 'apply', str(changes)], capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(catalogue.classify(self.data, {'journal': self.row['name']})['tier'], 'important')

    def test_codex_proposal_is_validated_without_saving_until_applied(self):
        app = SimpleNamespace(DATA=self.data, settings=lambda: {'selection_translation_model': 'mock-model'})
        config = {'model': 'mock-model', 'reasoning_effort': 'low'}
        for response, expected in (({'summary': '降为重要', 'changes': [self.change()]}, 'ready'),
                                   ({'summary': '缺少 ISSN', 'changes': []}, 'ready'),
                                   ({'summary': '错误', 'changes': [{'action': 'delete', 'issn': '0000-0000'}]}, 'failed')):
            with patch('codex_models.resolve_config', return_value=config), \
                 patch.object(codex_bridge, 'propose_journal_changes', return_value=response):
                job = catalogue.start_proposal(app, '修改名单')
                deadline = time.monotonic() + 2
                while job['status'] == 'running' and time.monotonic() < deadline:
                    time.sleep(.005)
                    job = catalogue.proposal(self.data, job['id'])
                self.assertEqual(job['status'], expected)
                self.assertFalse(catalogue.catalogue_path(self.data).exists())

    def test_http_edits_refresh_papers_reject_cross_origin_and_stale_writes(self):
        with patch.object(server, 'DATA', self.data), patch.object(server, 'LIBRARY', self.data / 'library'):
            server.init_db()
            server.put_doc({'id': '0123456789abcdef', 'hash': 'mock-hash', 'journal': self.row['name'], 'paper_type': 'journal'})
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path, method='GET', body=None, origin=None):
                headers = {'Content-Type': 'application/json'}
                if origin:
                    headers['Origin'] = origin
                # Origin rejection does not read the body; avoid a Windows TCP reset.
                req = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}' + path, method=method,
                                             data=b'' if origin else json.dumps(body).encode() if body is not None else None, headers=headers)
                with opener.open(req, timeout=3) as response:
                    return json.load(response)
            try:
                with patch.object(server, 'PORT', httpd.server_port):
                    base = request('/api/journal-catalogue')
                    body = {'revision': base['revision'], 'changes': [self.change()]}
                    request('/api/journal-catalogue', 'PATCH', body)
                    papers = request('/api/papers')
                    self.assertEqual(papers['papers'][0]['journal_tier'], 'important')
                    self.assertNotEqual(papers['journal_catalogue_revision'], base['revision'])
                    with self.assertRaises(urllib.error.HTTPError) as stale:
                        request('/api/journal-catalogue', 'PATCH', body)
                    self.assertEqual(stale.exception.code, 409)
                    with self.assertRaises(urllib.error.HTTPError) as cross:
                        request('/api/journal-catalogue', 'PATCH', body, 'https://example.com')
                    self.assertEqual(cross.exception.code, 403)
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
