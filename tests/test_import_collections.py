"""Import/topic lifecycle invariants on a temporary library with model responses mocked."""
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
            'collection': '待分类', 'collection_assignment': {'status': 'queued'}, 'notes': 'saved note',
            'blocks': [{'id': 'title-block', 'text': 'Optical Tactile Sensing for Aerial Robots', 'page': 1,
                        'kind': 'heading', 'bbox': [.1, .1, .8, .2], 'translation': '原译文', 'note': '原批注'}]}
        self.doc['reading_structure'] = paper_structure.organize(self.doc)
        server.put_doc(self.doc)

    def run_worker(self, refine):
        with patch.object(self.jobs, 'get', side_effect=[self.doc['id'], EOFError]), \
                patch.object(self.jobs, 'task_done'), \
                patch('codex_models.resolve_config', return_value={'model': 'mock', 'reasoning_effort': 'low'}), \
                patch.object(paper_structure, 'refine', side_effect=refine):
            with self.assertRaises(EOFError): server.structure_worker()

    def result(self, name, mode='create'):
        result = paper_structure.organize(self.doc)
        result.update(status='ready', model_config={'model': 'mock'},
            collection_plan={'mode': mode, 'name': name, 'reason': '论文研究机器人触觉感知。', 'evidence_ids': ['title']})
        return result

    def test_structure_and_collection_share_one_bounded_call(self):
        existing = {'collections': [{'name': '触觉机器人', 'examples': [{'title': 'Whisker-based tactile flight'}]}]}
        calls = []
        def fake(instruction, data, schema, **kwargs):
            calls.append(data)
            self.assertIn('研究主题归类输出契约', instruction)
            self.assertEqual(schema['required'], ['roles', 'collection'])
            self.assertEqual(data['collections'], existing['collections'])
            return {'roles': [{'id': b['id'], 'role': b['local_role']} for b in data['candidates']],
                'collection': {'mode': 'reuse', 'name': '触觉机器人', 'reason': '以触觉传感支持机器人接触控制。', 'evidence_ids': ['title']}}
        result = paper_structure.refine(self.doc, {'model': 'mock'}, runner=fake, collection_context=existing)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['collection_plan']['name'], '触觉机器人')
        self.assertEqual(result['status'], 'ready')

    def test_new_topic_is_saved_and_next_import_sees_it(self):
        saved = server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), self.result('触觉机器人'))
        self.assertEqual(saved['collection'], '触觉机器人')
        self.assertEqual(saved['collection_assignment']['status'], 'ready')
        self.assertEqual(server.settings()['collections'], ['触觉机器人'])
        context = server.import_collection_context()
        self.assertEqual(context['collections'][0]['name'], '触觉机器人')
        self.assertEqual(context['collections'][0]['examples'][0]['title'], self.doc['title'])
        self.assertEqual(saved['blocks'], self.doc['blocks'])
        self.assertEqual(saved['notes'], 'saved note')
        self.assertNotIn('collection_plan', saved['reading_structure'])

    def test_current_names_are_rechecked_before_commit(self):
        with server.db() as con:
            con.execute('INSERT INTO settings VALUES(?, ?)', ('collections', '["Tactile Robots"]'))
        saved = server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), self.result('tactile-robots'))
        self.assertEqual(saved['collection'], 'Tactile Robots')
        self.assertEqual(server.settings()['collections'], ['Tactile Robots'])

    def test_manual_move_and_manual_removal_both_win_over_background_results(self):
        for choice in ('用户主题', ''):
            server.put_doc(copy.deepcopy(self.doc))
            server.update_doc(self.doc['id'], lambda d: server.patch_paper_fields(d, {'collection': choice, 'notes': 'new note'}))
            saved = server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), self.result('触觉机器人'))
            self.assertEqual(saved['collection'], choice)
            self.assertEqual(saved['collection_assignment']['status'], 'manual')
            self.assertEqual(saved['notes'], 'new note')
            self.assertNotIn('触觉机器人', server.settings()['collections'])

    def test_import_queues_automatically_and_duplicate_keeps_manual_assignment(self):
        extracted = {'metadata': {'title': self.doc['title']}, 'blocks': self.doc['blocks'], 'pages': [{'number': 1}]}
        content = b'%PDF-1.7 test-import'
        with patch('pdf_tools.extract_document', return_value=copy.deepcopy(extracted)), patch.object(server, 'crossref_metadata', return_value={}):
            first = server.import_pdf(content, 'test.pdf')
            self.assertEqual(first['collection'], '待分类')
            self.assertEqual(first['collection_assignment']['status'], 'queued')
            self.assertEqual(self.jobs.qsize(), 1)
            server.update_doc(first['id'], lambda d: server.patch_paper_fields(d, {'collection': '手动主题'}))
            duplicate = server.import_pdf(content, 'different-name.pdf')
            self.assertTrue(duplicate['duplicate'])
            self.assertEqual(duplicate['id'], first['id'])
            self.assertEqual(duplicate['collection'], '手动主题')
            self.assertEqual(self.jobs.qsize(), 1)

    def test_missing_text_stays_pending_without_model_job(self):
        with patch('pdf_tools.extract_document', return_value={'metadata': {}, 'blocks': [], 'pages': [{'number': 1}]}):
            result = server.import_pdf(b'%PDF-1.7 scan', 'scan.pdf')
        self.assertEqual(result['collection_assignment']['status'], 'needs_review')
        self.assertEqual(result['collection'], '待分类')
        self.assertTrue(self.jobs.empty())

    def test_failed_call_preserves_pending_and_retry_is_single_flight(self):
        server.schedule_structure(self.doc['id'])
        self.run_worker(lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('offline')))
        saved = server.get_doc(self.doc['id'])
        self.assertEqual(saved['collection'], '待分类')
        self.assertEqual(saved['collection_assignment']['status'], 'needs_review')
        self.assertEqual(saved['collection_assignment']['error'], 'offline')
        self.assertFalse(server.STRUCTURE_QUEUED)
        self.jobs.get_nowait()  # Mock worker did not consume the physical queue.
        server.schedule_structure(self.doc['id']); server.schedule_structure(self.doc['id'])
        self.assertEqual(self.jobs.qsize(), 1)
        self.run_worker(lambda *args, **kwargs: self.result('触觉机器人'))
        self.assertEqual(server.get_doc(self.doc['id'])['collection'], '触觉机器人')

    def test_classification_can_be_retried_even_when_structure_is_ready(self):
        result = paper_structure.organize(self.doc)
        result.update(status='ready', collection_error='主题判断失败')
        server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), result)
        server.schedule_structure(self.doc['id'])
        self.assertEqual(self.jobs.qsize(), 1)

    def test_invalid_structure_does_not_discard_valid_collection(self):
        def fake(*args, **kwargs):
            return {'roles': [], 'collection': self.result('触觉机器人')['collection_plan']}
        result = paper_structure.refine(self.doc, {'model': 'mock'}, runner=fake, collection_context={'collections': []})
        self.assertEqual(result['status'], 'needs_review')
        saved = server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), result)
        self.assertEqual(saved['collection'], '触觉机器人')
        self.assertTrue(saved['reading_structure']['audit']['complete'])

    def test_unknown_collection_or_evidence_is_rejected_and_uncertain_is_preserved(self):
        context = {'collections': []}
        suggestion = self.result('未知主题', 'reuse')['collection_plan']
        with self.assertRaises(ValueError): paper_structure.validate_collection(context, suggestion, ['title'])
        suggestion.update(mode='create', evidence_ids=['invented-source'])
        with self.assertRaises(ValueError): paper_structure.validate_collection(context, suggestion, ['title'])
        suggestion.update(mode='uncertain', evidence_ids=[])
        checked = paper_structure.validate_collection(context, suggestion, ['title'])
        result = paper_structure.organize(self.doc); result.update(collection_plan=checked)
        saved = server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), result)
        self.assertEqual(saved['collection'], '待分类')
        self.assertEqual(saved['collection_assignment']['status'], 'needs_review')
        self.assertFalse(server.settings()['collections'])

    def test_deleted_paper_does_not_create_a_new_collection(self):
        server.update_doc(self.doc['id'], lambda d: d.update(deleted=True))
        with self.assertRaises(ValueError):
            server.commit_import_result(self.doc['id'], paper_structure.fingerprint(self.doc), self.result('触觉机器人'))
        self.assertFalse(server.settings()['collections'])


if __name__ == '__main__': unittest.main()
