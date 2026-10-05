"""Regression checks use an isolated SQLite library and mock all model calls."""
import copy
import json
import queue
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server
import codex_bridge
import codex_models
import paper_ai

PID = '0123456789abcdef'


def resolve(model='', reasoning_effort=''):
    model = model or 'model-a'
    effort = reasoning_effort or 'medium'
    if model not in ('model-a', 'model-b') or effort not in ('low', 'medium', 'high'):
        raise ValueError('模型或推理强度不可用')
    return {'model': model, 'reasoning_effort': effort}


class TranslationTests(unittest.TestCase):
    def setUp(self):
        work = (ROOT / 'work').resolve()
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='translation-tests-', dir=work)
        self.folder = Path(self.temp.name).resolve()
        self.assertTrue(self.folder.is_relative_to(work))
        self.patches = [patch.object(server, 'DATA', self.folder), patch.object(server, 'LIBRARY', self.folder / 'library'),
                        patch.object(server, 'JOBS', queue.Queue()), patch.object(server, 'QUEUED', set()),
                        patch.object(server, 'CANCEL', {}), patch.object(server, 'RESUME_REQUESTED', set()),
                        patch.object(server, 'QUOTA_PAUSED', threading.Event()),
                        patch.object(codex_models, 'resolve_config', side_effect=resolve)]
        for mock in self.patches:
            mock.start()
        server.init_db()
        self.doc = {'id': PID, 'hash': 'test-hash', 'title': 'Synthetic test document', 'deleted': False,
                    'pages': [{'number': 1}, {'number': 2}], 'figures': [], 'notes': 'paper note',
                    'blocks': [{'id': 'p1', 'page': 1, 'text': 'First synthetic paragraph.', 'note': 'first note', 'highlight': True},
                               {'id': 'p2', 'page': 2, 'text': 'Second synthetic paragraph.', 'note': 'second note'}],
                    'translation': {'status': 'idle', 'error': ''}, 'read_page': 1}
        server.put_doc(self.doc)

    def tearDown(self):
        for mock in reversed(self.patches):
            mock.stop()
        self.assertTrue(self.folder.is_relative_to((ROOT / 'work').resolve()))
        self.temp.cleanup()

    def defaults(self, model='model-b', effort='high'):
        with server.db() as con:
            for key, value in [('translation_model', model), ('translation_reasoning_effort', effort)]:
                con.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, json.dumps(value)))

    def unqueue(self):
        server.QUEUED.clear()
        while not server.JOBS.empty():
            server.JOBS.get_nowait()
            server.JOBS.task_done()

    def run_translation(self, event=None, callback=None):
        event = event or threading.Event()
        def translate(con_factory, pid, doc, batch, config, thread_id, **kwargs):
            if callback:
                callback(batch, kwargs)
            if kwargs.get('on_thread'):
                kwargs['on_thread'](thread_id or 'mock-thread')
            return thread_id or 'mock-thread', {b['id']: 'new ' + b['id'] for b in batch}
        with patch.object(paper_ai, 'translate_batch', side_effect=translate) as model_call:
            server.execute_translation(PID, event)
        self.unqueue()
        return model_call

    def switch_model(self, model, effort='medium'):
        with server.db() as con:
            paper_ai.switch_model(con, PID, {'model': model, 'reasoning_effort': effort},
                                  server.get_doc(PID), server.LIBRARY)

    def add_old_translations(self):
        self.doc['blocks'][0].update(translation='old manual p1', translation_manually_edited=True)
        self.doc['blocks'][1]['translation'] = 'old p2'
        self.doc['translation']['status'] = 'completed'
        server.put_doc(self.doc)

    def test_defaults_are_frozen_when_queued(self):
        server.enqueue(PID, 'translate')
        task = copy.deepcopy(server.get_doc(PID)['translation'])
        self.defaults()
        call = self.run_translation()
        self.assertEqual(call.call_args.args[4], {'model': 'model-a', 'reasoning_effort': 'medium'})
        self.assertEqual(server.get_doc(PID)['translation']['run_id'], task['run_id'])

    def test_legacy_partial_resume_does_not_relabel_old_text(self):
        self.doc['blocks'][0]['translation'] = 'historic translation'
        server.put_doc(self.doc)
        server.enqueue(PID, 'translate', {'model': 'model-b', 'reasoning_effort': 'high'})
        call = self.run_translation()
        self.assertEqual([b['id'] for b in call.call_args.args[3]], ['p2'])
        doc = server.get_doc(PID)
        self.assertEqual(doc['blocks'][0]['translation'], 'historic translation')
        self.assertNotIn('translation_run_id', doc['blocks'][0])
        self.assertIsNone(server.public_doc(doc)['translation']['current_config'])
        self.assertTrue(server.public_doc(doc)['translation']['current_mixed'])

    def test_changed_config_requires_explicit_retranslation(self):
        server.enqueue(PID, 'translate', {'model': 'model-a', 'reasoning_effort': 'low'})
        self.unqueue()
        before = server.get_doc(PID)
        with self.assertRaisesRegex(ValueError, '重新翻译'):
            server.enqueue(PID, 'translate', {'model': 'model-b', 'reasoning_effort': 'high'})
        self.assertEqual(server.get_doc(PID), before)

    def test_retranslation_stages_then_commits_and_restores_manual_version(self):
        self.add_old_translations()
        server.enqueue(PID, 'translate', {'model': 'model-b', 'reasoning_effort': 'high', 'retranslate': True})
        observed = []
        def inspect_current(batch, kwargs):
            observed.append([b['translation'] for b in server.get_doc(PID)['blocks']])
        self.run_translation(callback=inspect_current)
        self.assertEqual(observed, [['old manual p1', 'old p2']])
        doc = server.get_doc(PID)
        self.assertEqual([b['translation'] for b in doc['blocks']], ['new p1', 'new p2'])
        self.assertNotIn('staging', doc['translation'])
        version = doc['translation_versions'][0]
        self.assertIsNone(version['config'])
        self.assertTrue(version['blocks']['p1']['translation_manually_edited'])
        server.restore_translation(PID, version['id'])
        restored = server.get_doc(PID)
        self.assertEqual(restored['blocks'][0]['translation'], 'old manual p1')
        self.assertTrue(restored['blocks'][0]['translation_manually_edited'])
        self.assertEqual(restored['notes'], 'paper note')
        self.assertEqual(restored['blocks'][0]['note'], 'first note')
        self.assertTrue(restored['blocks'][0]['highlight'])
        self.assertEqual(restored['translation']['status'], 'completed')

    def test_pause_restart_resumes_only_unfinished_staging(self):
        self.add_old_translations()
        server.enqueue(PID, 'translate', {'model': 'model-b', 'reasoning_effort': 'high', 'retranslate': True})
        event = threading.Event()
        with patch.object(server, 'batches', side_effect=lambda blocks: ([b] for b in blocks)):
            self.run_translation(event, lambda batch, kwargs: event.set())
        paused = server.get_doc(PID)
        self.assertEqual(paused['translation']['status'], 'paused')
        self.assertEqual(paused['translation']['staging'], {'p1': 'new p1'})
        self.assertEqual(paused['blocks'][0]['translation'], 'old manual p1')
        server.init_db()
        self.defaults('model-a', 'low')
        server.enqueue(PID, 'translate')
        call = self.run_translation()
        self.assertEqual([b['id'] for b in call.call_args.args[3]], ['p2'])
        self.assertEqual(call.call_args.args[4]['model'], 'model-b')
        self.assertEqual(server.get_doc(PID)['blocks'][0]['translation'], 'new p1')

    def test_changing_paused_retranslation_archives_recoverable_draft(self):
        self.add_old_translations()
        server.enqueue(PID, 'translate', {'model': 'model-a', 'retranslate': True})
        server.update_doc(PID, lambda d: d['translation'].update(staging={'p1': 'draft a'}, status='paused'))
        self.unqueue()
        self.switch_model('model-b')
        server.enqueue(PID, 'translate', {'model': 'model-b', 'retranslate': True})
        doc = server.get_doc(PID)
        self.assertEqual(doc['translation']['staging'], {})
        self.assertEqual(doc['translation_drafts'][0]['task']['staging'], {'p1': 'draft a'})
        self.assertEqual(doc['blocks'][0]['translation'], 'old manual p1')
        self.unqueue()
        server.restore_translation_draft(PID, doc['translation_drafts'][0]['id'])
        recovered = server.get_doc(PID)['translation']
        self.assertEqual(recovered['config']['model'], 'model-a')
        self.assertEqual(recovered['staging'], {'p1': 'draft a'})
        self.assertEqual(recovered['status'], 'paused')

    def test_active_task_cannot_be_replaced(self):
        server.enqueue(PID, 'translate', {'model': 'model-a'})
        original = server.get_doc(PID)
        self.switch_model('model-b')
        with self.assertRaisesRegex(ValueError, '暂停'):
            server.enqueue(PID, 'translate', {'model': 'model-b', 'retranslate': True})
        self.assertEqual(server.get_doc(PID), original)

    def test_bad_batch_keeps_readable_version_and_no_partial_batch(self):
        self.add_old_translations()
        server.enqueue(PID, 'translate', {'retranslate': True})
        with patch.object(paper_ai, 'translate_batch', return_value=('mock-thread', {'p1': 'incomplete'})):
            with self.assertRaisesRegex(ValueError, '完整'):
                server.execute_translation(PID, threading.Event())
        doc = server.get_doc(PID)
        self.assertEqual(doc['blocks'][0]['translation'], 'old manual p1')
        self.assertEqual(doc['translation']['staging'], {})

    def test_readable_edit_during_retranslation_is_archived_at_commit(self):
        self.add_old_translations()
        server.enqueue(PID, 'translate', {'retranslate': True})
        def edit_during_batch(batch, kwargs):
            server.update_doc(PID, lambda d: d['blocks'][0].update(translation='new manual edit', note='new note', translation_manually_edited=True))
        self.run_translation(callback=edit_during_batch)
        doc = server.get_doc(PID)
        self.assertEqual(doc['translation_versions'][0]['blocks']['p1']['translation'], 'new manual edit')
        self.assertEqual(doc['blocks'][0]['note'], 'new note')

    def test_public_document_does_not_leak_staging_or_versions_content(self):
        self.add_old_translations()
        server.enqueue(PID, 'translate', {'retranslate': True})
        server.update_doc(PID, lambda d: d['translation'].update(staging={'p1': 'SECRET DRAFT'}))
        encoded = json.dumps(server.public_doc(server.get_doc(PID)))
        self.assertNotIn('SECRET DRAFT', encoded)
        self.assertNotIn('old manual', encoded)
        self.assertNotIn('translation_runs', encoded)
        self.assertTrue(server.public_doc(server.get_doc(PID))['translation']['has_staging'])

    def test_unavailable_model_does_not_mutate_task(self):
        before = server.get_doc(PID)
        with self.assertRaises(ValueError):
            server.enqueue(PID, 'translate', {'model': 'missing-model'})
        self.assertEqual(server.get_doc(PID), before)
        self.assertFalse(server.QUEUED)

    def test_checking_completed_translation_keeps_known_provenance(self):
        server.enqueue(PID, 'translate', {'model': 'model-a', 'reasoning_effort': 'high'})
        self.run_translation()
        self.switch_model('model-b', 'low')
        server.enqueue(PID, 'translate', {'model': 'model-b', 'reasoning_effort': 'low', 'retranslate': True})
        self.run_translation()
        old = server.get_doc(PID)['translation_versions'][0]
        server.restore_translation(PID, old['id'])
        server.enqueue(PID, 'translate')
        call = self.run_translation()
        call.assert_not_called()
        current = server.public_doc(server.get_doc(PID))['translation']
        self.assertEqual(current['current_config'], {'model': 'model-a', 'reasoning_effort': 'high'})
        self.assertFalse(current['current_mixed'])

    def test_restore_completed_version_preserves_pending_retranslation_draft(self):
        server.enqueue(PID, 'translate', {'model': 'model-a'})
        self.run_translation()
        self.switch_model('model-b')
        server.enqueue(PID, 'translate', {'model': 'model-b', 'retranslate': True})
        self.run_translation()
        old = server.get_doc(PID)['translation_versions'][0]
        self.switch_model('model-a', 'high')
        server.enqueue(PID, 'translate', {'model': 'model-a', 'reasoning_effort': 'high', 'retranslate': True})
        server.update_doc(PID, lambda d: d['translation'].update(staging={'p1': 'valuable draft'}, status='paused'))
        self.unqueue()
        server.restore_translation(PID, old['id'])
        doc = server.get_doc(PID)
        self.assertEqual(doc['translation_drafts'][0]['task']['staging'], {'p1': 'valuable draft'})
        self.assertNotIn('staging', doc['translation'])

    def test_restart_marks_running_job_paused_without_losing_snapshot(self):
        server.enqueue(PID, 'translate', {'model': 'model-b', 'reasoning_effort': 'high', 'retranslate': True})
        server.update_doc(PID, lambda d: d['translation'].update(status='running', staging={'p1': 'saved chunk'}))
        before = copy.deepcopy(server.get_doc(PID)['translation'])
        server.init_db()
        after = server.get_doc(PID)['translation']
        self.assertEqual(after['status'], 'paused')
        self.assertEqual(after['config'], before['config'])
        self.assertEqual(after['run_id'], before['run_id'])
        self.assertEqual(after['staging'], before['staging'])

    def test_reader_state_validation_and_independent_settings(self):
        state = {'version': 1, 'page': 2, 'original_offset': .5, 'translation_block_id': 'p2',
                 'translation_offset': .25, 'zoom': 125, 'split_ratio': .6, 'mode': 'parallel'}
        self.assertEqual(server.validate_reader_state(state, self.doc), state)
        for bad in ({'page': True}, {'page': 3}, {'original_offset': float('nan')},
                    {'translation_block_id': 'foreign'}, {'split_ratio': .9}, {'zoom': 99}, {'mode': 'bad'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                server.validate_reader_state(bad, self.doc)
        with patch.object(codex_models, 'resolve_config', side_effect=AssertionError('must not load model catalog')):
            self.assertEqual(server.validate_settings({'theme': 'sage', 'reader_split_ratio': .6}), {'theme': 'sage', 'reader_split_ratio': .6})

    def test_translate_bridge_forwards_snapshot_without_affecting_other_tasks(self):
        with patch.object(codex_bridge, '_run_json', return_value={'p1': '译文'}) as run:
            codex_bridge.translate_blocks([self.doc['blocks'][0]], model='model-b', reasoning_effort='high')
            self.assertEqual(run.call_args.kwargs['model'], 'model-b')
            self.assertEqual(run.call_args.kwargs['reasoning_effort'], 'high')

    def test_http_contract_health_reader_versions_and_model_refresh(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port_patch = patch.object(server, 'PORT', httpd.server_port)
        port_patch.start()
        base = 'http://127.0.0.1:' + str(httpd.server_port)
        def request(path, data=None, method=None):
            req = urllib.request.Request(base + path, json.dumps(data).encode() if data is not None else None,
                                         {'Content-Type': 'application/json'}, method=method)
            with urllib.request.urlopen(req) as response:
                return json.load(response)
        try:
            with patch.object(server, 'codex_status', side_effect=AssertionError('health must stay local')):
                health = request('/api/health')
            self.assertEqual(health['app_root'], str(ROOT))
            self.assertEqual(health['data_dir'], str(self.folder))
            with patch.object(codex_models, 'get', return_value={'models': []}) as get:
                self.assertEqual(request('/api/models?refresh=1'), {'models': []})
                get.assert_called_once_with(refresh=True)
            server.update_doc(PID, lambda doc: doc.update(read_state='read'))
            self.assertNotIn('read_state', request('/api/papers/' + PID)['paper'])
            paper = request('/api/papers/' + PID, {'reader_state': {'page': 2, 'original_offset': .6, 'mode': 'parallel'}}, 'PATCH')['paper']
            self.assertEqual(paper['read_page'], 2)
            self.assertEqual(paper['reader_state']['original_offset'], .6)
            self.assertNotIn('read_state', paper)
            self.assertEqual(server.get_doc(PID)['read_state'], 'read', 'Reading position must not alter legacy read flags.')
            request('/api/papers/' + PID + '/blocks/p1', {'translation': 'manual', 'note': 'kept'}, 'PATCH')
            self.assertTrue(server.get_doc(PID)['blocks'][0]['translation_manually_edited'])
            versions = request('/api/papers/' + PID + '/translation-versions')
            self.assertTrue(versions['versions'][0]['current'])
            self.assertNotIn('blocks', versions['versions'][0])
        finally:
            httpd.shutdown()
            thread.join(timeout=2)
            httpd.server_close()
            port_patch.stop()


if __name__ == '__main__':
    unittest.main()
