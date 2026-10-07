"""Manual collection ownership survives imports and background structure checks."""
import copy
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paper_structure
import server


class ImportCollectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        data = Path(self.folder.name)
        self.jobs = queue.Queue()
        for name, value in [('DATA', data), ('LIBRARY', data / 'library'), ('STRUCTURE_JOBS', self.jobs), ('STRUCTURE_QUEUED', set())]:
            p = patch.object(server, name, value)
            p.start(); self.addCleanup(p.stop)
        server.init_db()
        self.doc = {'id': 'a'*16, 'hash': 'test', 'title': 'Optical Tactile Sensing for Aerial Robots',
            'abstract': 'A tactile sensor enables contact control of aerial robots.', 'deleted': False,
            'collection': '', 'notes': 'saved note',
            'blocks': [{'id': 'title-block', 'text': 'Optical Tactile Sensing for Aerial Robots', 'page': 1,
                        'kind': 'heading', 'bbox': [.1, .1, .8, .2], 'translation': '原译文', 'note': '原批注'}]}
        self.doc['reading_structure'] = paper_structure.organize(self.doc)
        server.put_doc(self.doc)

    def run_worker(self, refine):
        with patch.object(self.jobs, 'get', side_effect=[self.doc['id'], EOFError]), \
                patch.object(self.jobs, 'task_done'), \
                patch('codex_models.resolve_config', return_value={'model': 'mock', 'reasoning_effort': 'low'}), \
                patch.object(paper_structure, 'refine', side_effect=refine) as call:
            with self.assertRaises(EOFError): server.structure_worker()
        return call

    def result(self):
        result = paper_structure.organize(self.doc)
        result.update(status='ready', model_config={'model': 'mock'})
        return result

    def test_model_request_only_classifies_source_roles(self):
        calls = []
        def fake(instruction, data, schema, **kwargs):
            calls.append(data)
            self.assertEqual(schema['required'], ['roles'])
            self.assertEqual(set(schema['properties']), {'roles'})
            self.assertEqual(set(data), {'candidates'})
            return {'roles': [{'id': b['id'], 'role': b['local_role']} for b in data['candidates']]}
        result = paper_structure.refine(self.doc, {'model': 'mock'}, runner=fake)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'ready')
        self.assertNotIn('collection_plan', result)

    def test_stale_topic_plan_cannot_assign_or_create_collection(self):
        result = self.result()
        result.update(collection_plan={'mode': 'create', 'name': '模型主题'}, collection_error='legacy')
        saved = server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), result)
        self.assertEqual(saved['collection'], '')
        self.assertFalse(server.settings()['collections'])
        self.assertEqual(saved['blocks'], self.doc['blocks'])
        self.assertEqual(saved['notes'], 'saved note')
        self.assertNotIn('collection_plan', saved['reading_structure'])
        self.assertNotIn('collection_error', saved['reading_structure'])

    def test_existing_collection_and_history_preserved_but_not_exposed_as_active_feature(self):
        history = {'status': 'ready', 'reason': '历史主题判断', 'mode': 'reuse', 'name': '旧合集'}
        server.update_doc(self.doc['id'], lambda d: d.update(collection='旧合集', collection_assignment=history))
        with server.db() as con:
            con.execute('INSERT INTO settings VALUES(?, ?)', ('collections', '["旧合集"]'))
        saved = server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), self.result())
        self.assertEqual(saved['collection'], '旧合集')
        self.assertEqual(saved['collection_assignment'], history)
        self.assertEqual(server.settings()['collections'], ['旧合集'])
        for full in (False, True):
            public = server.public_doc(saved, full=full)
            self.assertEqual(public['collection'], '旧合集')
            self.assertNotIn('collection_assignment', public)
        self.assertEqual(saved['collection_assignment'], history)

    def test_manual_move_and_removal_survive_background_commit(self):
        for choice in ('用户主题', ''):
            server.put_doc(copy.deepcopy(self.doc))
            server.update_doc(self.doc['id'], lambda d: server.patch_paper_fields(d, {'collection': choice, 'notes': 'new note'}))
            saved = server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), self.result())
            self.assertEqual(saved['collection'], choice)
            self.assertEqual(saved['collection_assignment']['status'], 'manual')
            self.assertEqual(saved['notes'], 'new note')

    def test_import_unassigned_and_duplicate_keeps_manual_assignment(self):
        extracted = {'metadata': {'title': self.doc['title']}, 'blocks': self.doc['blocks'], 'pages': [{'number': 1}]}
        with patch('pdf_tools.extract_document', return_value=copy.deepcopy(extracted)):
            first = server.import_pdf(b'%PDF-1.7 test-import', 'test.pdf')
            self.assertEqual(first['collection'], '')
            self.assertNotIn('collection_assignment', first)
            self.assertFalse(server.settings()['collections'])
            self.assertEqual(self.jobs.qsize(), 1, 'Source structure check must still run')
            server.update_doc(first['id'], lambda d: server.patch_paper_fields(d, {'collection': '手动主题'}))
            duplicate = server.import_pdf(b'%PDF-1.7 test-import', 'different-name.pdf')
            self.assertTrue(duplicate['duplicate'])
            self.assertEqual(duplicate['id'], first['id'])
            self.assertEqual(duplicate['collection'], '手动主题')
            self.assertEqual(self.jobs.qsize(), 1)

    def test_scan_unassigned_without_model_job(self):
        with patch('pdf_tools.extract_document', return_value={'metadata': {}, 'blocks': [], 'pages': [{'number': 1}]}):
            result = server.import_pdf(b'%PDF-1.7 scan', 'scan.pdf')
        self.assertEqual(result['collection'], '')
        self.assertNotIn('collection_assignment', result)
        self.assertTrue(self.jobs.empty())

    def test_worker_no_collection_context_and_no_membership_change(self):
        server.schedule_structure(self.doc['id'])
        call = self.run_worker(lambda *args, **kwargs: self.result())
        self.assertEqual(call.call_args.kwargs, {})
        saved = server.get_doc(self.doc['id'])
        self.assertEqual(saved['reading_structure']['status'], 'ready')
        self.assertEqual(saved['collection'], '')
        self.assertNotIn('collection_assignment', saved)
        self.assertFalse(server.settings()['collections'])

    def test_failure_and_retry_only_affect_source_structure(self):
        server.schedule_structure(self.doc['id'])
        self.run_worker(lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('offline')))
        saved = server.get_doc(self.doc['id'])
        self.assertEqual(saved['collection'], '')
        self.assertNotIn('collection_assignment', saved)
        self.assertEqual(saved['reading_structure']['status'], 'needs_review')
        self.assertEqual(saved['reading_structure']['error'], 'offline')
        self.assertFalse(server.STRUCTURE_QUEUED)
        self.jobs.get_nowait()  # Mock worker did not consume the physical queue.
        server.schedule_structure(self.doc['id']); server.schedule_structure(self.doc['id'])
        self.assertEqual(self.jobs.qsize(), 1)
        self.run_worker(lambda *args, **kwargs: self.result())
        self.assertEqual(server.get_doc(self.doc['id'])['collection'], '')

    def test_legacy_classification_marker_does_not_trigger_new_model_call(self):
        server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), self.result())
        server.update_doc(self.doc['id'], lambda d: d.update(collection_assignment={'status': 'queued'}, collection='待分类'))
        server.schedule_structure(self.doc['id'])
        self.assertTrue(self.jobs.empty())
        self.assertEqual(server.get_doc(self.doc['id'])['collection'], '待分类')

    def test_deleted_paper_cannot_create_collection(self):
        server.update_doc(self.doc['id'], lambda d: d.update(deleted=True))
        with self.assertRaises(ValueError):
            server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), self.result())
        self.assertFalse(server.settings()['collections'])


if __name__ == '__main__': unittest.main()
