"""Observable structure/provenance invariants, without real model requests."""
import copy
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paper_structure
import server


def block(bid, text, page=1, kind='paragraph', bbox=None, **extra):
    return dict(id=bid, text=text, page=page, kind=kind,
                bbox=bbox or [.1, .3, .45, .4], **extra)


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.doc = {'title': 'Optical Touch in Robots', 'authors': ['Alice Smith', 'Bob Chen'], 'blocks': [
            block('title1', 'Optical Touch', kind='heading', bbox=[.1, .1, .7, .13]),
            block('title2', 'in Robots', kind='heading', bbox=[.1, .14, .5, .17]),
            block('authors', 'Alice Smith1, Bob Chen2*', bbox=[.1, .2, .7, .23]),
            block('intro', '1. Introduction', kind='heading', bbox=[.55, .3, .9, .32]),
            block('abstract-label', 'A B S T R A C T', bbox=[.1, .3, .3, .32]),
            block('abstract', 'We study optical tactile sensing and evaluate its accuracy in a robot.', bbox=[.1, .34, .45, .5]),
            block('body1', 'The robot uses a camera to measure', bbox=[.55, .34, .9, .4], translation='机器人使用相机测量', note='saved note'),
            block('body2', 'contact forces.', bbox=[.55, .41, .9, .44]),
            block('aff', '1 Department of Engineering, Example University. 2 Robotics Laboratory.', bbox=[.1, .8, .45, .86]),
            block('doi', 'DOI: 10.1234/example', bbox=[.1, .88, .45, .9]),
            block('page', '1', bbox=[.4, .96, .5, .98]),
            block('methods', '2. Methods', page=2, kind='heading', bbox=[.1, .1, .4, .13]),
            block('equation', '[公式：请对照原文图像]', page=2, is_formula=True, image='equation.jpg'),
        ]}

    def test_front_information_precedes_body_without_rewriting_sources(self):
        before = copy.deepcopy(self.doc)
        result = paper_structure.organize(self.doc)
        self.assertEqual([s['role'] for s in result['sections'][:5]], ['title', 'authors', 'affiliations', 'article_info', 'abstract'])
        self.assertEqual(result['sections'][0]['entries'][0]['source_ids'], ['title1', 'title2'])
        self.assertEqual(self.doc, before)
        self.assertTrue(result['audit']['complete'])

    def test_natural_paragraph_keeps_translations_and_notes_on_original_ids(self):
        result = paper_structure.organize(self.doc)
        body = next(s for s in result['sections'] if s['label'] == '1. Introduction')
        self.assertEqual(body['entries'][1]['source_ids'], ['body1', 'body2'])
        self.assertEqual(self.doc['blocks'][6]['note'], 'saved note')
        self.assertEqual(self.doc['blocks'][6]['translation'], '机器人使用相机测量')
        equation = next(e for s in result['sections'] for e in s['entries'] if e['id'] == 'equation')
        self.assertEqual(equation['role'], 'equation')

    def test_cross_page_continuation_is_one_paragraph(self):
        d = {'blocks': [block('a', 'We measured the', bbox=[.55, .85, .9, .92]),
                        block('b', 'contact force at 10 Hz.', page=2, bbox=[.1, .1, .45, .2])]}
        self.assertEqual(paper_structure.organize(d)['sections'][0]['entries'][0]['source_ids'], ['a', 'b'])

    def test_missing_or_duplicate_provenance_is_rejected(self):
        result = paper_structure.organize(self.doc)
        result['sections'][0]['entries'][0]['source_ids'].append('body1')
        with self.assertRaises(ValueError): paper_structure.audit_structure(self.doc, result)

    def test_model_cannot_hide_real_body_or_invent_ids(self):
        selected = [{'id': 'body1'}]
        for payload in ([{'id': 'body1', 'role': 'furniture'}], [{'id': 'fake', 'role': 'paragraph'}], []):
            with self.assertRaises(ValueError):
                paper_structure.validate_roles(self.doc, selected, {'roles': payload})

    def test_semantic_runner_loads_skill_and_preserves_every_block(self):
        def fake(instruction, data, schema, **kwargs):
            self.assertIn('EzRead 论文导入', instruction)
            self.assertIn('语义分类输出契约', instruction)
            return {'roles': [{'id': b['id'], 'role': b['local_role']} for b in data['candidates']]}
        result = paper_structure.refine(self.doc, {'model': 'mock', 'reasoning_effort': 'low'}, runner=fake)
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['audit']['complete'])

    def test_selection_requires_the_same_untranslated_natural_paragraph(self):
        doc = copy.deepcopy(self.doc)
        doc['blocks'][6].pop('translation')
        doc['reading_structure'] = paper_structure.organize(doc)
        self.assertIn('measure contact forces', paper_structure.selection_source(doc, 'body1', ['body1', 'body2']))
        with self.assertRaises(ValueError): paper_structure.selection_source(doc, 'body1', ['body1', 'doi'])
        with self.assertRaises(ValueError): paper_structure.selection_source(self.doc, 'body1', ['body1', 'body2'])

    def test_empty_scan_does_not_invent_abstract_or_authors(self):
        result = paper_structure.organize({'blocks': []})
        self.assertFalse(result['sections'])
        self.assertIn('OCR', result['warnings'][0])

    def test_short_prose_at_the_top_of_next_page_is_never_removed(self):
        d = {'blocks': [block('body', 'contact force was measured at 10 Hz.', page=2, bbox=[.1, .055, .45, .075])]}
        s = paper_structure.organize(d)
        self.assertFalse(s['omitted'])
        self.assertEqual(s['sections'][0]['entries'][0]['source_ids'], ['body'])

    def test_auto_queue_is_single_flight_and_background_commit_preserves_latest_notes(self):
        import queue
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            jobs = queue.Queue()
            with patch.object(server, 'DATA', data), patch.object(server, 'LIBRARY', data / 'library'), \
                    patch.object(server, 'STRUCTURE_JOBS', jobs), patch.object(server, 'STRUCTURE_QUEUED', set()):
                server.init_db()
                doc = copy.deepcopy(self.doc)
                doc.update(id='a'*16, hash='test', deleted=False)
                server.put_doc(doc)
                server.schedule_structure(doc['id']); server.schedule_structure(doc['id'])
                self.assertEqual(jobs.qsize(), 1)
                self.assertEqual(server.get_doc(doc['id'])['reading_structure']['status'], 'queued')
                def fake_refine(snapshot, config):
                    server.update_doc(doc['id'], lambda d: d.update(notes='written during semantic check'))
                    result = paper_structure.organize(snapshot)
                    result.update(status='ready', method='local+ai')
                    return result
                with patch.object(jobs, 'get', side_effect=[doc['id'], EOFError]), \
                        patch.object(jobs, 'task_done'), \
                        patch('codex_models.resolve_config', return_value={'model':'mock','reasoning_effort':'low'}), \
                        patch.object(paper_structure, 'refine', side_effect=fake_refine):
                    with self.assertRaises(EOFError): server.structure_worker()
                saved = server.get_doc(doc['id'])
                self.assertEqual(saved['notes'], 'written during semantic check')
                self.assertEqual(saved['reading_structure']['status'], 'ready')
                self.assertFalse(server.STRUCTURE_QUEUED)


if __name__ == '__main__': unittest.main()
