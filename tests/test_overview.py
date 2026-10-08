"""Offline overview contracts, persistence, failed updates and legacy handoff."""
import contextlib
import copy
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import codex_bridge
import codex_models
import paper_ai
import server
from ezread.overview import overview_text

PID = '0123456789abcdef'


def generated(sections=None):
    return {'title_zh': '合成理论研究', 'summary': '给出可证明的稳定条件。',
            'tags': ['稳定性', '理论推导'], 'overview_sections': sections if sections is not None else [
                {'title': '核心命题与适用假设', 'content': '在给定假设下证明稳定条件。'},
                {'title': '证明思路', 'content': '构造能量函数并推导收敛条件。'}]}


class OverviewContractTests(unittest.TestCase):
    def test_headings_count_and_order_are_not_prescribed(self):
        variants = [generated(), generated([{'title': '领域发展脉络', 'content': '梳理已有路线与证据。'}])]
        for variant in variants:
            with self.subTest(titles=[s['title'] for s in variant['overview_sections']]), \
                    patch.object(codex_bridge, '_run_json', return_value=variant) as call:
                result = codex_bridge.summarize_paper('Synthetic source', model='mock')
                self.assertEqual(result, variant)
                instruction, payload, schema = call.call_args.args
                self.assertEqual(payload, {'paper_text': 'Synthetic source'})
                self.assertEqual(set(schema['required']), {'title_zh', 'summary', 'tags', 'overview_sections'})
                section_schema = schema['properties']['overview_sections']['items']
                self.assertNotIn('enum', section_schema['properties']['title'])
                self.assertEqual(set(section_schema['required']), {'title', 'content'})
                self.assertIn('Do not follow a predefined outline', instruction)

    def test_incomplete_response_is_rejected_as_a_whole(self):
        malformed = [None, {}, generated([]), generated([{'title': '只有标题'}]),
                     generated([{'title': '', 'content': '正文'}]),
                     dict(generated(), overview_sections='invalid'), dict(generated(), tags='invalid')]
        for result in malformed:
            with self.subTest(result=result), patch.object(codex_bridge, '_run_json', return_value=result):
                with self.assertRaises(codex_bridge.TranslationError) as caught:
                    codex_bridge.summarize_paper('Synthetic source')
                self.assertEqual(caught.exception.code, 'invalid_response')

    def test_legacy_overview_keeps_populated_content_only(self):
        text = overview_text({'summary': '旧概述', 'problem': '', 'method': '旧方法', 'results': ''})
        self.assertEqual(text, '旧概述\n\n## 方法与装置\n\n旧方法')
        self.assertNotIn('尚未生成', text)


class OverviewTaskTests(unittest.TestCase):
    def setUp(self):
        work = ROOT / 'work'
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='overview-tests-', dir=work)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.jobs = queue.Queue()
        for name, value in (
            ('DATA', self.folder), ('LIBRARY', self.folder / 'library'), ('JOBS', self.jobs),
            ('QUEUED', set()), ('CANCEL', {}), ('RESUME_REQUESTED', set()),
            ('QUOTA_PAUSED', threading.Event()), ('SHUTDOWN', threading.Event())
        ):
            mock = patch.object(server, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(codex_models, 'resolve_config', side_effect=lambda model='', reasoning_effort='':
                            {'model': model or 'mock', 'reasoning_effort': reasoning_effort or 'low'})
        mock.start()
        self.addCleanup(mock.stop)
        server.init_db()
        self.doc = {'id': PID, 'hash': 'synthetic-overview', 'title': 'Synthetic theory paper',
                    'deleted': False, 'notes': '用户笔记', 'summary': '旧概述', 'method': '旧方法',
                    'tags': ['旧标签'], 'translation': {'status': 'idle'}, 'pages': [],
                    'overview_sections': [{'title': '旧分节', 'content': '原有内容'}],
                    'blocks': [{'id': 'b1', 'page': 1, 'text': 'Synthetic theorem.'},
                               {'id': 'b2', 'page': 1, 'text': 'References.', 'kind': 'reference'}]}
        server.put_doc(self.doc)

    def run_summary(self, result, cancel=False):
        server.enqueue(PID, 'summarize')
        self.jobs.put(None)
        def respond(*args, **kwargs):
            if cancel:
                kwargs['cancel_event'].set()
            return result
        with patch.object(codex_bridge, '_run_json', side_effect=respond) as call, \
                contextlib.redirect_stderr(io.StringIO()):
            server.worker()
        return call

    def test_sections_round_trip_and_reach_history_and_model_handoff(self):
        result = generated()
        call = self.run_summary(result)
        doc = server.get_doc(PID)
        self.assertEqual(doc['summarize_status'], 'completed')
        self.assertEqual(doc['overview_sections'], result['overview_sections'])
        self.assertEqual(doc['method'], '旧方法')
        self.assertEqual(doc['notes'], self.doc['notes'])
        self.assertEqual(doc['blocks'], self.doc['blocks'])
        self.assertEqual(call.call_args.args[1], {'paper_text': 'Synthetic theorem.'})
        self.assertEqual(server.public_doc(doc, full=True)['overview_sections'], result['overview_sections'])
        with server.db() as con:
            messages = paper_ai.history(con, PID)['messages']
            self.assertEqual(messages[-1]['kind'], 'summary')
            self.assertEqual(messages[-1]['text'], overview_text(result))
            paper_ai.switch_model(con, PID, {'model': 'second-mock'}, doc, server.LIBRARY)
        handoff = json.loads((server.LIBRARY / PID / 'ai-handoff-2.json').read_text(encoding='utf-8'))
        self.assertEqual(handoff['summary'], overview_text(result))
        self.assertNotIn('旧方法', handoff['summary'])

    def test_failed_or_cancelled_update_preserves_existing_content(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                before = copy.deepcopy(server.get_doc(PID))
                result = generated() if cancel else generated([{'title': '不完整分节'}])
                self.run_summary(result, cancel=cancel)
                saved = server.get_doc(PID)
                self.assertEqual(saved['summarize_status'], 'error')
                for key in ('summary', 'overview_sections', 'tags', 'method', 'notes', 'blocks'):
                    self.assertEqual(saved[key], before[key])
                with server.db() as con:
                    self.assertEqual(paper_ai.history(con, PID)['messages'], [])


if __name__ == '__main__':
    unittest.main()
