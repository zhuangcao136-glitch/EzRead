"""Range annotations and revisions use a temporary SQLite library and fake models."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import server
from ezread import text_actions as actions
import codex_bridge
import codex_models
import paper_ai

PID = 'f0123456789abcde'


class TextActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='text-actions-', dir=Path.cwd() / 'work')
        self.folder = Path(self.temp.name)
        self.patches = [patch.object(server, 'DATA', self.folder), patch.object(server, 'LIBRARY', self.folder / 'library'),
                        patch.object(server, 'QUEUED', set()), patch.object(server, 'codex_status', return_value={'authenticated': True}),
                        patch.object(codex_models, 'resolve_config', return_value={'model': 'mock', 'reasoning_effort': 'low'})]
        for item in self.patches: item.start()
        server.init_db()
        self.doc = {'id': PID, 'hash': 'immutable-pdf-hash', 'title': 'Synthetic selection test', 'deleted': False,
                    'pages': [{'number': 1}], 'figures': [], 'translation': {'status': 'idle'}, 'blocks': [
                        {'id': 'one', 'page': 1, 'text': 'First 😀 sentence. Another sentence.', 'translation': '第一😀句。第二句。', 'note': 'old note', 'highlight': True},
                        {'id': 'two', 'page': 1, 'text': 'Second paragraph.', 'translation': '另一个自然段。'}]}
        server.put_doc(self.doc)

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.assertTrue(self.folder.resolve().is_relative_to((Path.cwd() / 'work').resolve()))
        self.temp.cleanup()

    def selection(self, bid='one', language='original', quote='sentence'):
        block = next(b for b in server.get_doc(PID)['blocks'] if b['id'] == bid)
        text = actions.text_of(block, language); start = text.index(quote)
        return {'block_id': bid, 'language': language, 'start': start, 'end': start + len(quote), 'quote': quote, 'base_text': text}

    def apply(self, action, ranges=None, **fields):
        data = {'action': action, **fields}
        if ranges is not None: data['ranges'] = ranges
        return actions.apply(server, PID, data)

    def test_cross_paragraph_note_is_one_record_and_keeps_legacy_fields(self):
        doc = self.apply('note', [self.selection(), self.selection('two', quote='Second')], note='one cross paragraph note')
        self.assertEqual(len(doc['text_annotations']), 1)
        self.assertEqual(len(doc['text_annotations'][0]['ranges']), 2)
        self.assertEqual(doc['blocks'][0]['note'], 'old note')
        self.assertTrue(doc['blocks'][0]['highlight'])

    def test_highlight_colors_persist_and_note_edits_preserve_red(self):
        self.apply('highlight', [self.selection()])
        doc = self.apply('highlight', [self.selection(quote='Another')], color='red')
        item = doc['text_annotations'][-1]
        self.apply('edit_annotation', annotation_id=item['id'], modified_at=item['modified_at'],
                   revision=item['revision'], previous_note='', note='red annotation')
        saved = actions.resolved_annotations(server.get_doc(PID))
        self.assertEqual([a['color'] for a in saved], ['yellow', 'red'])
        self.assertEqual(saved[-1]['note'], 'red annotation')
        self.assertTrue(all(a['status'] == 'resolved' for a in saved))

    def test_invalid_highlight_color_does_not_write_annotations(self):
        for color in ('blue', None, [], {}):
            with self.subTest(color=color), self.assertRaises(ValueError):
                self.apply('highlight', [self.selection()], color=color)
        self.assertEqual(server.get_doc(PID).get('text_annotations', []), [])

    def test_source_revision_preserves_pdf_text_and_existing_translation_without_inference(self):
        with patch.object(codex_bridge, 'translate_selection') as inference:
            doc = self.apply('revise', [self.selection(quote='First')], replacement='Revised')
        inference.assert_not_called()
        self.assertEqual(doc['hash'], self.doc['hash'])
        self.assertEqual(doc['blocks'][0]['text'], self.doc['blocks'][0]['text'])
        self.assertTrue(actions.original_text(doc['blocks'][0]).startswith('Revised'))
        self.assertEqual(doc['blocks'][0]['translation'], self.doc['blocks'][0]['translation'])
        self.assertTrue(actions.review_needed(doc['blocks'][0]))

    def test_translation_revision_changes_only_selected_codepoints_and_has_history(self):
        doc = self.apply('revise', [self.selection(language='translation', quote='😀')], replacement='修订')
        self.assertEqual(doc['blocks'][0]['translation'], '第一修订句。第二句。')
        self.assertTrue(doc['blocks'][0]['translation_manually_edited'])
        self.assertEqual(doc['translation_versions'][0]['blocks']['one']['translation'], self.doc['blocks'][0]['translation'])
        self.assertEqual(len(doc['text_revision_history']), 1)

    def test_stale_selection_is_rejected_even_if_selected_word_still_matches(self):
        selection = self.selection()
        server.update_doc(PID, lambda d: d['blocks'][0].update(text_override=d['blocks'][0]['text'] + ' appended'))
        with self.assertRaises(actions.TextConflict): self.apply('revise', [selection], replacement='bad')
        self.assertEqual(len(server.get_doc(PID).get('text_revision_history', [])), 0)

    def test_foreign_ids_boolean_offsets_and_wrong_quotes_are_rejected(self):
        for fields in ({'block_id': 'foreign'}, {'start': True}, {'quote': 'not source'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.apply('highlight', [{**self.selection(), **fields}])

    def test_exact_quote_reanchors_after_a_prefix_is_inserted(self):
        doc = self.apply('highlight', [self.selection(quote='Another')])
        server.update_doc(PID, lambda d: d['blocks'][0].update(text_override='Inserted. ' + d['blocks'][0]['text']))
        annotation = actions.resolved_annotations(server.get_doc(PID))[0]
        self.assertEqual(annotation['status'], 'resolved')
        self.assertEqual(annotation['ranges'][0]['start'], doc['text_annotations'][0]['ranges'][0]['start'] + len('Inserted. '))

    def test_changed_or_ambiguous_quote_is_retained_as_needs_review(self):
        self.apply('note', [self.selection(quote='Another')], note='keep this note')
        server.update_doc(PID, lambda d: d['blocks'][0].update(text_override='Another Another'))
        annotation = actions.resolved_annotations(server.get_doc(PID))[0]
        self.assertEqual(annotation['status'], 'needs_review')
        self.assertEqual(annotation['quote'], 'Another')
        self.assertEqual(annotation['note'], 'keep this note')

    def test_english_and_chinese_ranges_are_independent(self):
        self.apply('highlight', [self.selection(language='translation', quote='第二句')])
        self.apply('revise', [self.selection(quote='First')], replacement='Corrected')
        self.assertEqual(actions.resolved_annotations(server.get_doc(PID))[0]['status'], 'resolved')

    def test_restore_is_reversible_and_rejects_overwriting_later_edits(self):
        first = self.apply('revise', [self.selection(quote='First')], replacement='Edited')
        revision = first['text_revision_history'][0]['id']
        self.apply('revise', [self.selection(quote='Another')], replacement='New')
        with self.assertRaises(actions.TextConflict): self.apply('restore_revision', revision_id=revision)
        current = server.get_doc(PID)
        self.apply('restore_revision', revision_id=current['text_revision_history'][-1]['id'])
        restored = self.apply('restore_revision', revision_id=revision)
        self.assertEqual(actions.original_text(restored['blocks'][0]), self.doc['blocks'][0]['text'])

    def test_request_retry_does_not_duplicate_note_or_source_revision(self):
        selected = self.selection()
        self.apply('note', [selected], note='retry', request_id='retry-note')
        doc = self.apply('note', [selected], note='retry', request_id='retry-note')
        self.assertEqual(len(doc['text_annotations']), 1)
        self.apply('revise', [selected], replacement='edit', request_id='retry-revision')
        doc = self.apply('revise', [selected], replacement='edit', request_id='retry-revision')
        self.assertEqual(len(doc['text_revision_history']), 1)

    def test_selected_translation_uses_validated_corrected_source(self):
        self.apply('revise', [self.selection(quote='First')], replacement='Changed')
        selected = self.selection(quote='Changed')
        with patch.object(codex_bridge, 'translate_selection', return_value='模拟译文') as inference:
            result = actions.translate(server, PID, {'ranges': [selected], 'selected_text': 'client cannot replace source'})
        self.assertEqual(inference.call_args.args[0], 'Changed')
        self.assertEqual(inference.call_args.kwargs['paper_id'], PID)
        self.assertEqual(result['translation'], '模拟译文')

    def test_dictionary_translation_never_checks_login_or_calls_model(self):
        before = server.get_doc(PID)
        with patch.object(server, 'codex_status', side_effect=AssertionError('Offline lookup must not check login')), \
             patch.object(codex_bridge, 'translate_selection', side_effect=AssertionError('Offline lookup must not infer')):
            result = actions.translate_word(server, PID, {'ranges': [self.selection(quote='First')], 'selected_text': 'untrusted client text'})
        self.assertTrue(result['found'])
        self.assertEqual(result['method'], 'dictionary')
        self.assertEqual(server.get_doc(PID), before)

    def test_sentence_response_cannot_survive_source_revision(self):
        selected = self.selection(quote='First')
        def revise_while_waiting(*args, **kwargs):
            self.apply('revise', [selected], replacement='Newer')
            return 'stale result'
        with patch.object(codex_bridge, 'translate_selection', side_effect=revise_while_waiting), self.assertRaises(actions.TextConflict):
            actions.translate(server, PID, {'ranges': [selected], 'selected_text': 'First'})

    def test_http_progress_is_visible_during_inference_and_retained_after_failure(self):
        from ezread.selection_requests import SelectionRequests
        started, release = threading.Event(), threading.Event()
        result = []
        before = server.get_doc(PID)
        data = {'action': 'translate_sentence', 'ranges': [self.selection()], 'selected_text': 'sentence',
                'client_id': 'client-progress-0123456789', 'request_id': 'request-progress-0123456789'}
        listener = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        base = f'http://127.0.0.1:{listener.server_port}/api/papers/{PID}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def model(*args, **kwargs):
            kwargs['on_progress']({'kind': 'diagnostic', 'text': '网络不可用，正在重连 1/5'})
            kwargs['on_progress']({'kind': 'output', 'text': '部分译文'})
            started.set()
            release.wait(2)
            raise codex_bridge.TranslationError('网络连接失败。', code='network')
        def post():
            req = urllib.request.Request(base + '/text-actions', data=json.dumps(data).encode(),
                                         headers={'Content-Type': 'application/json'})
            try:
                opener.open(req, timeout=3)
            except urllib.error.HTTPError as error:
                result.append((error.code, json.load(error)))
        def progress(**extra):
            query = urllib.parse.urlencode({key: data[key] for key in ('client_id', 'request_id')} | extra)
            with opener.open(base + '/selection-progress?' + query, timeout=2) as response:
                return json.load(response)
        with patch.object(server, 'PORT', listener.server_port), \
             patch.object(server, 'SELECTION_REQUESTS', SelectionRequests()), \
             patch.object(codex_bridge, 'translate_selection', side_effect=model):
            host = threading.Thread(target=listener.serve_forever, daemon=True); host.start()
            worker = threading.Thread(target=post); worker.start()
            try:
                self.assertTrue(started.wait(1))
                running = progress()
                self.assertFalse(running['done'])
                self.assertEqual(running['output'], '部分译文')
                self.assertIn('网络不可用', running['events'][-1]['text'])
                self.assertEqual(progress(request_id='another-request-0123456789')['events'], [])
                release.set(); worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result[0][0], 502)
                final = progress()
                self.assertTrue(final['done'])
                self.assertEqual(final['events'][-1]['text'], '网络连接失败。')
                self.assertEqual(len(final['events']), 1)
                self.assertEqual(server.get_doc(PID), before)
            finally:
                release.set(); worker.join(3)
                listener.shutdown(); listener.server_close(); host.join()

    def test_retranslate_uses_revised_english_and_keeps_old_snapshot(self):
        self.apply('revise', [self.selection(quote='First')], replacement='Corrected')
        with patch.object(paper_ai, 'translate_batch', return_value=('mock-thread', {'one': '完整的新译文'})) as inference:
            doc = actions.retranslate(server, PID, {'ranges': [self.selection(quote='Corrected')]})
        self.assertTrue(inference.call_args.args[3][0]['text'].startswith('Corrected'))
        self.assertFalse(actions.review_needed(doc['blocks'][0]))
        self.assertEqual(doc['translation_versions'][-1]['blocks']['one']['translation'], self.doc['blocks'][0]['translation'])

    def test_retranslate_cannot_overwrite_a_concurrent_manual_translation(self):
        def concurrent(*args, **kwargs):
            server.update_doc(PID, lambda d: d['blocks'][0].update(translation='a new manual edit'))
            return 'mock', {'one': 'stale model result'}
        with patch.object(paper_ai, 'translate_batch', side_effect=concurrent), self.assertRaises(actions.TextConflict):
            actions.retranslate(server, PID, {'ranges': [self.selection()]})
        self.assertEqual(server.get_doc(PID)['blocks'][0]['translation'], 'a new manual edit')

    def test_invalid_language_and_action_are_clean_validation_errors(self):
        with self.assertRaises(ValueError): self.apply('highlight', [{**self.selection(), 'language': []}])
        with self.assertRaises(ValueError): self.apply([])

    def test_note_revision_token_rejects_two_edits_within_one_timestamp(self):
        doc = self.apply('note', [self.selection()], note='before')
        item = doc['text_annotations'][0]
        fields = {'annotation_id': item['id'], 'modified_at': item['modified_at'], 'revision': item['revision'], 'previous_note': 'before'}
        self.apply('edit_annotation', note='after', **fields)
        with self.assertRaises(actions.TextConflict): self.apply('edit_annotation', note='stale', **fields)
        self.assertEqual(server.get_doc(PID)['text_annotations'][0]['note'], 'after')

    def test_full_retranslation_keeps_new_range_edit_and_staged_model_result(self):
        doc = server.get_doc(PID)
        server.prepare_translation(doc, {'retranslate': True, 'model': 'mock', 'reasoning_effort': 'low'})
        server.put_doc(doc)
        def concurrent(con_factory, pid, doc, batch, config, thread_id, **kwargs):
            self.apply('revise', [self.selection(language='translation', quote='第一')], replacement='手动修订')
            return 'mock-thread', {b['id']: 'stale model result' for b in batch}
        with patch.object(paper_ai, 'translate_batch', side_effect=concurrent), self.assertRaisesRegex(ValueError, '未覆盖新内容'):
            server.execute_translation(PID, threading.Event())
        current = server.get_doc(PID)
        self.assertTrue(current['blocks'][0]['translation'].startswith('手动修订'))
        self.assertEqual(current['translation']['staging']['one'], 'stale model result')

    def test_http_range_endpoint_checks_origin_and_exposes_conflict_as_409(self):
        listener = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        with patch.object(server, 'PORT', listener.server_port):
            thread = threading.Thread(target=listener.serve_forever, daemon=True); thread.start()
            url = f'http://127.0.0.1:{listener.server_port}/api/papers/{PID}/text-actions'
            payload = {'action': 'highlight', 'ranges': [self.selection()]}
            def request(value, origin=None):
                headers = {'Content-Type': 'application/json'}
                if origin: headers['Origin'] = origin
                # Origin rejection does not read the body; avoid a Windows TCP reset.
                return urllib.request.urlopen(urllib.request.Request(url, data=b'' if origin else json.dumps(value).encode(), headers=headers), timeout=3)
            try:
                with request(payload) as response: self.assertEqual(len(json.load(response)['paper']['text_annotations']), 1)
                with self.assertRaises(urllib.error.HTTPError) as error: request(payload, 'https://untrusted.example')
                self.assertEqual(error.exception.code, 403)
                server.update_doc(PID, lambda d: d['blocks'][0].update(text_override='changed source'))
                with self.assertRaises(urllib.error.HTTPError) as error: request(payload)
                self.assertEqual(error.exception.code, 409)
            finally: listener.shutdown(); listener.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
