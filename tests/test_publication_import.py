"""Offline publication extraction, consent and library-preserving enrichment."""
import copy
import json
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paper_metadata
import server
from ezread import publication

TITLE = 'TacPalm: A Soft Gripper with a Biomimetic Optical Tactile Palm for Stable Precise Grasping'
AUTHORS = ['Xuyang Zhang', 'Tianqi Yang', 'Dandan Zhang', 'Nathan F. Lepora']


def crossref_item(**overrides):
    return {'title': [TITLE], 'DOI': '10.1109/JSEN.2024.3471812', 'type': 'journal-article',
        'container-title': ['IEEE Sensors Journal'], 'author': [{'given': 'Xuyang', 'family': 'Zhang'},
        {'given': 'Tianqi', 'family': 'Yang'}, {'given': 'Dandan', 'family': 'Zhang'}, {'given': 'Nathan F.', 'family': 'Lepora'}],
        'published': {'date-parts': [[2024, 11, 15]]}, 'published-online': {'date-parts': [[2024, 10, 7]]},
        'volume': '24', 'issue': '22', 'page': '38402-38416', **overrides}


def line(text, y):
    return {'text': text, 'bbox': [50, y, 550, y + 10]}


class LocalPublicationTests(unittest.TestCase):
    def extract(self, header, extra=(), raw=None, footer=None):
        lines = [line(header, 25), line(TITLE, 90), line(', '.join(AUTHORS), 145),
                 line('Abstract—A soft tactile gripper.', 180), *[line(text, 200 + i * 15) for i, text in enumerate(extra)]]
        blocks = [{'kind': 'heading', 'text': TITLE, 'bbox': [.1, .1, .9, .16]}]
        return paper_metadata.extract(raw or {}, blocks, lines, 800, footer_numbers=footer)

    def test_ieee_complete_is_ready_without_lookup(self):
        result = self.extract('IEEE SENSORS JOURNAL, VOL. 24, NO. 22, 15 NOVEMBER 2024',
            ['DOI: 10.1109/JSEN.2024.3471812'], footer=(38402, 38416, 15))
        self.assertEqual(result['journal'], 'IEEE Sensors Journal')
        self.assertEqual(result['authors'], AUTHORS)
        self.assertEqual(result['publication_date'], '2024-11-15')
        self.assertEqual(result['page_range'], '38402-38416')
        self.assertEqual(result['volume'], '24')
        self.assertEqual(result['issue'], '22')
        self.assertFalse(paper_metadata.missing_fields(result))
        self.assertEqual(publication.assessment(result)['status'], 'complete')

    def test_placeholder_and_file_creation_do_not_make_a_publication_date(self):
        result = self.extract('IEEE SENSORS JOURNAL, VOL. XX, NO. XX, XXXX XXXX',
            ['Received 23 September 2024', 'Accepted 1 October 2024', 'arXiv:2409.15239v1'],
            raw={'CreationDate': 'D:20240924020504Z'}, footer=(1, 14, 14))
        self.assertEqual(result['authors'], AUTHORS)
        self.assertIsNone(result['year'])
        self.assertFalse(result.get('page_range'))
        self.assertEqual(result['arxiv_id'], '2409.15239')
        self.assertEqual([m['key'] for m in paper_metadata.missing_fields(result)], ['publication_date', 'doi', 'page_range'])

    def test_references_and_dates_are_not_publication_metadata(self):
        result = self.extract('IEEE SENSORS JOURNAL, VOL. XX', ['I. INTRODUCTION',
            'Published work in 2025 motivated this.', '10.1234/reference.2025'])
        self.assertFalse(result['doi'])
        # A prose occurrence of "Published" must not count as a labeled publication date.
        self.assertIsNone(result['year'])

    def test_reversed_arxiv_margin(self):
        result = self.extract('IEEE SENSORS JOURNAL, VOL. XX', ['1v93251.9042:viXra'])
        self.assertEqual(result['arxiv_id'], '2409.15239')

    def test_year_precision_and_article_number_are_valid(self):
        result = self.extract('Nature Communications | (2024) 15', ['DOI: 10.1038/example', 'Article number: 12345'])
        self.assertEqual(result['publication_date'], '2024')
        self.assertEqual(result['article_number'], '12345')
        self.assertFalse(paper_metadata.missing_fields(result))

    def test_letter_spaced_header_multiline_title_and_copyright_margin(self):
        split = TITLE.index('Optical')
        lines = [line('S c i e n c e R o b o t i c s | R e s e a r c h', 30),
                 {**line('MACHINE LEARNING', 60), 'size': 9},
                 {**line(TITLE[:split], 80), 'size': 18},
                 {**line(TITLE[split:], 100), 'size': 18},
                 {'text': 'copyright © 2024 the', 'bbox': [570, 110, 650, 120]},
                 line(', '.join(AUTHORS[:2]) + ',', 130),
                 {'text': 'licensee american', 'bbox': [570, 135, 650, 145]},
                 line(', '.join(AUTHORS[2:]), 145),
                 line('This unlabeled abstract explains the sensing method and its performance.', 170),
                 line('Zhang et al., Sci. Robot. 9, eexample123 (2024) 26 June 2024', 765),
                 line('1 of 11', 780)]
        blocks = [{'kind': 'heading', 'text': l['text'], 'bbox': l['bbox']} for l in lines[:4]]
        raw = {'Title': TITLE, 'Subject': 'Sci. Robot. 2024.9:eexample123', 'Author': 'copyright © the'}
        result = paper_metadata.extract(raw, blocks, lines, 800,
            first_text='Science RoBoticS | ReSeaRch ARTICLE\nMACHINE LEARNING')
        self.assertEqual(result['authors'], AUTHORS)
        self.assertEqual(result['journal'], 'Science Robotics')
        self.assertEqual(result['publication_date'], '2024-06-26')
        self.assertEqual(result['volume'], '9')
        self.assertEqual(result['article_number'], 'eexample123')
        self.assertFalse(result['page_range'])
        # With absent Title metadata, typography must still beat the section label.
        result = paper_metadata.extract({}, blocks, lines, 800)
        self.assertEqual(result['authors'], AUTHORS)
        # Stored structure may merge the title while line extraction still has two rows.
        merged = blocks[:2] + [{'kind': 'heading', 'text': TITLE, 'bbox': [.08, .1, .9, .14]}]
        margin = {'text': 'Publishing Society', 'bbox': [570, 140, 650, 150]}
        result = paper_metadata.extract(raw, merged, [*lines, margin], 800)
        self.assertEqual(result['authors'], AUTHORS)

    def test_copyright_year_cannot_become_publication_year(self):
        lines = [line('Science Robotics | Research article', 30), line('copyright © 2024 the', 50)]
        result = paper_metadata.extract({}, [], lines, 800)
        self.assertIsNone(result['year'])
        self.assertFalse(result['authors'])

    def test_invalid_imported_authors_are_missing_and_filtered(self):
        invalid = ['copyright © the', 'licensee american', 'association for the']
        self.assertEqual(paper_metadata.valid_authors(invalid), [])
        self.assertEqual(paper_metadata.valid_authors([*invalid, 'Maria Bauzá', 'Nikhil Chavan- Dafle1']),
                         ['Maria Bauzá', 'Nikhil Chavan-Dafle'])
        self.assertIn('authors', [m['key'] for m in paper_metadata.missing_fields({'authors': invalid})])

    def test_conference_headers_preserve_full_name_and_identify_abbreviation(self):
        cases = [('2023 IEEE 19th International Conference on Automation Science and Engineering (CASE)', 'CASE'),
                 ('2024 7th International Conference on Soft Robotics (RoboSoft)', 'RoboSoft'),
                 ('2024 IEEE International Conference on Robotics and Automation', 'ICRA')]
        for name, abbreviation in cases:
            with self.subTest(venue=name):
                result = self.extract(name)
                self.assertEqual(result['paper_type'], 'conference')
                self.assertEqual(result['conference_name'], name)
                self.assertEqual(result['conference_abbr'], abbreviation)
                self.assertFalse(result['journal'])
                self.assertEqual(result['year'], int(name[:4]))
                self.assertEqual(result['metadata_provenance']['paper_type']['source'], 'pdf')

    def test_unknown_conference_abbreviation_is_not_invented(self):
        name = 'International Conference on Novel Experimental Devices'
        self.assertEqual(paper_metadata.conference_abbreviation(name), '')
        self.assertEqual(paper_metadata.conference_abbreviation(name + ' (ICNED 2024)'), 'ICNED')
        self.assertEqual(paper_metadata.conference_abbreviation(name, 'MyConf'), 'MyConf')
        self.assertEqual(paper_metadata.conference_abbreviation(name + ' (IEEE)'), '')
        self.assertEqual(paper_metadata.conference_abbreviation('IEEE'), '')

    def test_title_and_byline_are_shared_across_different_page_layouts(self):
        samples = [
            ('A New Method for Robot Perception', {}, [
                {**line('Journal of Engineering Research', 30), 'size': 24},
                {**line('journal homepage: example.invalid', 60), 'size': 8},
                {**line('A New Method for', 100), 'size': 18},
                {**line('Robot Perception', 122), 'size': 18}]),
            ('多模态机器人感知技术研究', {'Title': 'exporter-2024-001..24'}, [
                {**line('自动化学报', 30), 'size': 10},
                {**line('多模态机器人感知技术研究', 100), 'size': 20}]),
            ('Learning Motion Prediction in Complex Environments', {}, [
                {**line('Submitted to Transportation Research', 30), 'size': 12},
                {**line('Learning Motion Prediction', 100), 'size': 12},
                {**line('in Complex Environments', 116), 'size': 12}]),
            ('Reliable Sensing for Autonomous Vehicles', {'Author': 'Adobe PDFMaker'}, [
                {**line('Reliable Sensing for', 100), 'size': 20},
                {'text': 'Autonomous', 'bbox': [50, 122, 200, 142], 'size': 20},
                {'text': 'Vehicles', 'bbox': [210, 122, 400, 142], 'size': 20}])]
        for expected, raw, title_lines in samples:
            with self.subTest(title=expected):
                bottom = max(l['bbox'][3] for l in title_lines)
                names = ['张三', '李四'] if '机器人' in expected else ['Alice Brown', 'Bob Smith']
                lines = [*title_lines, {**line(', '.join(names), bottom + 22), 'size': 12},
                         {**line('Abstract—This study evaluates the proposed method.', bottom + 55), 'size': 10}]
                evidence = paper_metadata.title_evidence(raw, [], lines, 800)
                self.assertEqual(evidence['title'], expected)
                result = paper_metadata.extract(raw, [], lines, 800, title=evidence['title'])
                self.assertEqual(result['authors'], names)

    def test_title_does_not_absorb_sidebar_or_repeated_keyword(self):
        title = 'Multimodal Sensing for Reliable Robot Manipulation'
        lines = [{**line('Multimodal Sensing for', 90), 'size': 18},
                 {'text': 'Article reuse guidelines:', 'bbox': [580, 100, 700, 110], 'size': 8},
                 {**line('Reliable Robot Manipulation', 112), 'size': 18},
                 {**line('Alice Brown, Bob Smith', 150), 'size': 12},
                 {**line('A B S T R A C T', 180), 'size': 9},
                 {**line('Multimodal Sensing', 220), 'size': 9}]
        for raw in ({}, {'Title': title}):
            evidence = paper_metadata.title_evidence(raw, [], lines, 800)
            self.assertEqual(evidence['title'], title)
            self.assertEqual(evidence['bottom'], 122)
            self.assertEqual(paper_metadata.extract(raw, [], lines, 800, title=title)['authors'], ['Alice Brown', 'Bob Smith'])

    def test_author_noise_and_affiliation_markers_are_not_people(self):
        self.assertEqual(paper_metadata.valid_authors(['Accepted: 12 November 2025', 'Research Centre for Medical Robotics',
            'Shenzhen Institutes of Advanced Technology', 'Anonymous Submission', 'prediction accuracy. To overcome these challenges']), [])
        self.assertEqual(paper_metadata.valid_authors('Alice Brown a,b, Bob Smith c,*'), ['Alice Brown', 'Bob Smith'])
        self.assertEqual(paper_metadata.valid_authors('Alice Brown1 · Bob Smith2'), ['Alice Brown', 'Bob Smith'])

    def test_stale_complete_status_is_reassessed_without_searching(self):
        with patch.object(publication, '_read') as request:
            result = publication.assessment({'metadata_enrichment': {'status': 'complete'}, 'journal': 'Submitted to A Journal',
                'metadata_provenance': {'journal': {'source': 'pdf'}}})
            self.assertEqual(result['status'], 'needs_consent')
            self.assertIn('journal', [field['key'] for field in result['missing']])
            self.assertEqual(publication.assessment({'metadata_enrichment': {'status': 'declined'}})['status'], 'declined')
            request.assert_not_called()


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        data = Path(self.folder.name)
        for name, value in [('DATA', data), ('LIBRARY', data / 'library'), ('STRUCTURE_JOBS', queue.Queue()), ('STRUCTURE_QUEUED', set())]:
            p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)
        server.init_db()
        self.doc = {'id': 'b' * 16, 'hash': 'fixture', 'title': TITLE, 'authors': AUTHORS, 'journal': '',
            'year': None, 'doi': '', 'paper_type': 'other', 'notes': '手工笔记', 'read_page': 3,
            'collection': '手动主题', 'collection_assignment': {'status': 'manual'},
            'pages': [{'number': 1}], 'blocks': [{'id': 'p1-b1', 'text': 'Original prose',
                'page': 1, 'kind': 'paragraph', 'bbox': [.1, .1, .9, .2], 'translation': '手工译文', 'note': '批注'}]}
        server.put_doc(self.doc)

    def test_false_or_missing_consent_never_searches(self):
        with patch.object(publication, '_read') as request:
            for bad in ({}, {'consent': 1}, {'consent': 'true'}, {'consent': True, 'extra': 1}):
                with self.assertRaises(ValueError): server.enrich_publication(self.doc['id'], bad)
            result = server.enrich_publication(self.doc['id'], {'consent': False})
            request.assert_not_called()
        self.assertEqual(result['metadata_enrichment']['status'], 'declined')
        self.assertEqual(result['blocks'], self.doc['blocks'])

    def test_public_conference_label_derives_abbreviation_without_mutating_document(self):
        doc = {**self.doc, 'paper_type': 'conference', 'conference_name':
               '2023 IEEE International Conference on Automation Science and Engineering (CASE)', 'conference_abbr': ''}
        before = copy.deepcopy(doc)
        result = server.public_doc(doc)
        self.assertEqual(result['conference_abbr'], 'CASE')
        self.assertEqual(result['conference_name'], doc['conference_name'])
        self.assertEqual(doc, before)

    def test_legacy_pdf_conference_header_corrects_display_but_preserves_manual_type(self):
        doc = {**self.doc, 'paper_type': 'journal', 'journal': '2024 International Conference on Soft Robotics (Robosoft)',
               'metadata_provenance': {'journal': {'source': 'pdf'}}}
        before = copy.deepcopy(doc)
        result = server.public_doc(doc)
        self.assertEqual(result['paper_type'], 'conference')
        self.assertEqual(result['conference_abbr'], 'RoboSoft')
        self.assertEqual(result['conference_name'], doc['journal'])
        self.assertEqual(result['journal_tier'], 'conference')
        self.assertEqual(doc, before)
        server.patch_paper_fields(doc, {'paper_type': 'journal'})
        self.assertEqual(server.public_doc(doc)['paper_type'], 'journal')
        server.patch_paper_fields(doc, {'paper_type': 'conference', 'conference_abbr': 'Custom'})
        self.assertEqual(server.public_doc(doc)['conference_abbr'], 'Custom')
        server.patch_paper_fields(doc, {'conference_abbr': ''})
        self.assertEqual(server.public_doc(doc)['conference_abbr'], '')

    def test_search_fills_card_preserves_reading_data_and_persists(self):
        with patch.object(publication, '_read', return_value={'message': {'items': [crossref_item()]}}) as request:
            result = server.enrich_publication(self.doc['id'], {'consent': True})
            self.assertEqual(request.call_count, 1)
            self.assertIn('query.bibliographic=', request.call_args.args[0])
        self.assertEqual(result['journal'], 'IEEE Sensors Journal')
        self.assertEqual(result['year'], 2024)
        self.assertEqual(result['publication_date'], '2024-11-15')
        self.assertEqual(result['online_date'], '2024-10-07')
        self.assertEqual(result['page_range'], '38402-38416')
        self.assertEqual(result['paper_type'], 'journal')
        for key in ('blocks', 'notes', 'read_page', 'collection', 'collection_assignment'):
            self.assertEqual(result[key], self.doc[key])
        self.assertEqual(result['metadata_enrichment']['status'], 'complete')
        self.assertEqual(server.get_doc(self.doc['id'])['metadata_source'], result['metadata_source'])

    def test_import_never_calls_external_metadata_even_if_doi_present(self):
        extracted = {'metadata': {'title': TITLE, 'doi': '10.1109/JSEN.2024.3471812'},
            'pages': [{'number': 1}], 'blocks': [], 'figures': []}
        with patch('pdf_tools.extract_document', return_value=extracted), patch.object(server, 'crossref_metadata') as old, patch.object(publication, '_read') as request:
            result = server.import_pdf(b'%PDF-1.7 mocked', 'test.pdf')
            old.assert_not_called(); request.assert_not_called()
        self.assertEqual(result['metadata_enrichment']['status'], 'needs_consent')

    def test_wrong_paper_and_author_are_rejected(self):
        for item in (crossref_item(title=['TacTip tactile sensor']), crossref_item(author=[{'family': 'Unrelated'}])):
            with patch.object(publication, '_read', return_value={'message': {'items': [item]}}):
                result = server.enrich_publication(self.doc['id'], {'consent': True})
            self.assertEqual(result['metadata_enrichment']['status'], 'error')
            self.assertEqual(result['journal'], '')

    def test_exact_title_accepts_invalid_authors_but_valid_conflict_is_explicit(self):
        invalid = ['copyright © the', 'licensee american', 'association for the']
        self.assertEqual(publication._matches({**self.doc, 'authors': invalid}, crossref_item()), 1)
        accent = crossref_item(author=[{'given': 'Maria', 'family': 'Bauza'}])
        self.assertEqual(publication._matches({**self.doc, 'authors': ['Maria Bauzá']}, accent), 1)
        with patch.object(publication, '_read', return_value={'message': {'items': [crossref_item(author=[{'family': 'Unrelated'}])]}}):
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        self.assertIn('作者信息不一致', result['metadata_enrichment']['error'])
        self.assertFalse(result['doi'])

    def test_chinese_full_names_match_split_bibliographic_names_but_not_other_authors(self):
        title = '多模态机器人感知技术研究'
        doc = {**self.doc, 'title': title, 'authors': ['张三', '李四']}
        item = crossref_item(title=[title], author=[{'given': '三', 'family': '张'}, {'given': '四', 'family': '李'}])
        self.assertEqual(publication._matches(doc, item), 1)
        self.assertEqual(publication._matches(doc, crossref_item(title=[title], author=[{'given': '五', 'family': '王'}])), 0)
        with patch.object(publication, '_read', return_value={'message': {'items': [item]}}):
            self.assertEqual(publication.lookup(doc)['doi'], item['DOI'])

    def test_search_repairs_malformed_automatic_fields_and_refines_date(self):
        old = {'authors': ['copyright © the', 'licensee american'],
               'journal': 'IEEE Sensors Journal 2024.24:38402', 'publication_date': '2024', 'year': 2024}
        server.update_doc(self.doc['id'], lambda d: d.update(**old,
            metadata_provenance={key: {'source': 'pdf'} for key in old}))
        with patch.object(publication, '_read', return_value={'message': {'items': [crossref_item()]}}) as request:
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        self.assertNotIn('copyright', request.call_args.args[0])
        self.assertNotIn('licensee', request.call_args.args[0])
        self.assertEqual(result['authors'], AUTHORS)
        self.assertEqual(result['journal'], 'IEEE Sensors Journal')
        self.assertEqual(result['publication_date'], '2024-11-15')
        self.assertEqual(set(result['metadata_enrichment']['corrected']), {'authors', 'journal', 'publication_date'})
        self.assertEqual(result['metadata_enrichment']['status'], 'complete')
        for key in ('blocks', 'notes', 'read_page', 'collection', 'collection_assignment'):
            self.assertEqual(result[key], self.doc[key])

    def test_manual_invalid_authors_and_partial_date_are_preserved(self):
        manual = {'authors': ['copyright © the'], 'publication_date': '2024', 'year': 2024}
        server.update_doc(self.doc['id'], lambda d: server.patch_paper_fields(d, manual))
        with patch.object(publication, '_read', return_value={'message': {'items': [crossref_item()]}}):
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        for key, value in manual.items():
            self.assertEqual(result[key], value)
        self.assertEqual(result['metadata_enrichment']['status'], 'partial')

    def test_concurrent_invalid_author_edit_is_preserved(self):
        server.update_doc(self.doc['id'], lambda d: d.update(authors=['copyright © the']))
        def find(doc):
            server.update_doc(doc['id'], lambda d: d.update(authors=['New Author']))
            return publication.record(crossref_item())
        with patch.object(publication, 'lookup', side_effect=find):
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        self.assertEqual(result['authors'], ['New Author'])

    def test_null_remote_authors_do_not_crash(self):
        item = crossref_item(author=None)
        self.assertEqual(publication._matches({**self.doc, 'authors': []}, item), 1)
        self.assertEqual(publication.record(item)['authors'], [])

    def test_ambiguous_results_are_not_committed(self):
        with patch.object(publication, '_read', return_value={'message': {'items': [crossref_item(), crossref_item(DOI='10.1234/another')]}}):
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        self.assertIn('多个', result['metadata_enrichment']['error'])
        self.assertFalse(result['doi'])

    def test_network_failure_and_retry_are_bounded(self):
        with patch.object(publication, '_read', side_effect=TimeoutError()) as request:
            result = server.enrich_publication(self.doc['id'], {'consent': True})
            self.assertEqual(request.call_count, 1)
        self.assertEqual(result['metadata_enrichment']['status'], 'error')
        with patch.object(publication, '_read', return_value={'message': {'items': [crossref_item()]}}):
            self.assertEqual(server.enrich_publication(self.doc['id'], {'consent': True})['metadata_enrichment']['status'], 'complete')

    def test_manual_fields_including_blank_and_concurrent_edits_win(self):
        server.update_doc(self.doc['id'], lambda d: server.patch_paper_fields(d, {'journal': '用户期刊', 'page_range': ''}))
        def find(doc):
            server.update_doc(doc['id'], lambda d: server.patch_paper_fields(d, {'year': 2023}))
            return publication.record(crossref_item())
        with patch.object(publication, 'lookup', side_effect=find):
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        self.assertEqual(result['journal'], '用户期刊')
        self.assertEqual(result['year'], 2023)
        self.assertEqual(result['page_range'], '')
        self.assertEqual(result['metadata_enrichment']['status'], 'partial')

    def test_partial_record_keeps_unavailable_fields_unknown(self):
        item = crossref_item(page='', DOI='10.1234/fixture', published={})
        item.pop('published-online')
        with patch.object(publication, '_read', return_value={'message': {'items': [item]}}):
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        self.assertIsNone(result['year'])
        self.assertFalse(result.get('page_range'))
        self.assertEqual(result['metadata_enrichment']['status'], 'partial')

    def test_different_conference_imports_keep_type_and_abbreviation_through_enrichment(self):
        venues = [('2024 IEEE International Conference on Robotics and Automation (ICRA)', 'ICRA'),
                  ('2024 International Conference on Soft Robotics (RoboSoft)', 'RoboSoft'),
                  ('2024 IEEE International Conference on Automation Science and Engineering (CASE)', 'CASE')]
        for index, (venue, acronym) in enumerate(venues):
            with self.subTest(venue=venue):
                title = f'A General Robotic Sensing Method with Experiment Number {index}'
                lines = [{**line(venue, 25), 'size': 9}, {**line(title, 90), 'size': 18},
                         {**line('Alice Brown, Bob Smith', 130), 'size': 12}, line('Abstract—Experimental results.', 170)]
                metadata = {'title': title, **paper_metadata.extract({}, [], lines, 800, title=title)}
                extracted = {'metadata': metadata, 'pages': [{'number': 1}], 'blocks': copy.deepcopy(self.doc['blocks']), 'figures': []}
                item = crossref_item(title=[title], DOI=f'10.1234/workflow-{index}', type='proceedings-article',
                    **{'container-title': [venue], 'author': [{'given': 'Alice', 'family': 'Brown'}, {'given': 'Bob', 'family': 'Smith'}]})
                with patch('pdf_tools.extract_document', return_value=extracted), patch.object(publication, '_read') as request:
                    imported = server.import_pdf(f'%PDF-1.7 workflow-{index}'.encode(), f'conference-{index}.pdf')
                    request.assert_not_called()
                self.assertEqual(imported['conference_abbr'], acronym)
                self.assertEqual(imported['collection'], '')
                before_blocks = server.get_doc(imported['id'])['blocks']
                with patch.object(publication, '_read', return_value={'message': {'items': [item]}}):
                    server.enrich_publication(imported['id'], {'consent': True})
                stored = server.get_doc(imported['id'])
                self.assertEqual(stored['paper_type'], 'conference')
                self.assertEqual(stored['conference_abbr'], acronym)
                self.assertEqual(stored['conference_name'], venue)
                self.assertEqual(stored['blocks'], before_blocks)
                self.assertEqual(server.public_doc(stored)['conference_abbr'], acronym)

    def test_verified_conference_repairs_legacy_storage_and_respects_manual_type(self):
        venue = '2024 International Conference on Soft Robotics (RoboSoft)'
        server.update_doc(self.doc['id'], lambda d: d.update(journal=venue, paper_type='journal',
            metadata_provenance={'journal': {'source': 'pdf'}, 'paper_type': {'source': 'pdf'}}))
        item = crossref_item(type='proceedings-article', **{'container-title': [venue]})
        with patch.object(publication, '_read', return_value={'message': {'items': [item]}}):
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        self.assertEqual(result['journal'], '')
        self.assertEqual(result['paper_type'], 'conference')
        self.assertEqual(result['conference_abbr'], 'RoboSoft')
        self.assertTrue({'journal', 'paper_type'} <= set(result['metadata_enrichment']['corrected']))
        self.assertEqual(server.get_doc(self.doc['id'])['conference_abbr'], 'RoboSoft')
        for key in ('blocks', 'notes', 'read_page', 'collection'):
            self.assertEqual(result[key], self.doc[key])

        # A type edit during lookup must also stop the grouped legacy repair.
        server.put_doc({**self.doc, 'journal': venue, 'paper_type': 'journal',
            'metadata_provenance': {'journal': {'source': 'pdf'}}})
        def find(doc):
            server.update_doc(doc['id'], lambda d: server.patch_paper_fields(d, {'paper_type': 'journal'}))
            return publication.record(item)
        with patch.object(publication, 'lookup', side_effect=find):
            result = server.enrich_publication(self.doc['id'], {'consent': True})
        self.assertEqual(result['paper_type'], 'journal')
        self.assertEqual(result['journal'], venue)


if __name__ == '__main__': unittest.main()
