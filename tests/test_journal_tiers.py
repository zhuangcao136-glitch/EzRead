import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import journal_tiers


class JournalTierTests(unittest.TestCase):
    def test_catalogue_sources_are_recorded_and_cas_snapshot_is_frozen(self):
        pinned = ('https://github.com/yuzhounh/Authoritative-Journal-Classification/'
                  'blob/a5caae3954b49610dd5f357ca8d4a107abee829b/')
        for row in journal_tiers.catalogue_rows():
            with self.subTest(issn=row['issn']):
                self.assertTrue(row['source_url'].startswith('https://'))
                if row['basis'].startswith('2025中科院'):
                    self.assertTrue(row['source_url'].startswith(pinned))
                else:
                    self.assertIn(row['basis'], {'学院2019顶级名单', '学院2019重要名单',
                                                 '机械运载2019顶级名单', '机械运载2019重要名单'})
                    self.assertIn('bit.edu.cn/', row['source_url'])

    def test_school_list_is_complete_and_takes_precedence(self):
        rows = journal_tiers.catalogue_rows()
        self.assertEqual(len(rows), len({row['issn'] for row in rows}))
        self.assertEqual(Counter(row['basis'] for row in rows
                                 if row['basis'].startswith('学院2019')),
                         {'学院2019顶级名单': 29, '学院2019重要名单': 5})
        top = '''0376-0421 0731-5090 0094-5765 0894-1777 1270-9638
                 0018-9251 0001-1452 0748-4658 1540-7489 0035-8711
                 0890-6955 0360-5442 0016-2361 1063-6536 0010-2180
                 0888-3270 1552-3098 0360-3199 0378-3820 0963-0252
                 0017-9310 1359-4311 1615-147X 1050-0472 0022-460X
                 1087-1357 1007-5704 0005-1098 0018-9286'''.split()
        important = '1000-6893 1000-1328 1000-1093 0577-6686 1674-7259'.split()
        self.assertEqual(len(top), 29)
        self.assertEqual(len(important), 5)
        for issn in top:
            with self.subTest(issn=issn):
                self.assertEqual(journal_tiers.classify({'journal_issn': issn})['tier'], 'top')
        for issn in important:
            with self.subTest(issn=issn):
                self.assertEqual(journal_tiers.classify({'journal_issn': issn})['tier'], 'important')
        self.assertEqual(journal_tiers.classify({'journal': 'AIAA Journal'})['tier'], 'top')
        self.assertEqual(journal_tiers.classify({'journal': '机械工程学报'})['tier'], 'important')

    def test_frozen_2025_journal_examples(self):
        self.assertEqual(journal_tiers.classify({'journal': 'T-MECH'})['tier'], 'top')
        self.assertEqual(journal_tiers.classify({'journal_abbr': 'RA-L'})['tier'], 'important')
        self.assertEqual(journal_tiers.classify({'journal': 'Nature Communications'})['tier'], 'top')
        self.assertEqual(journal_tiers.classify({'journal': 'Unknown Journal'})['tier'], 'other')
        # User-confirmed precedence: the 2025 big-category Top result supersedes
        # the 2019 mechanical important listing for this specific journal.
        self.assertEqual(journal_tiers.classify({'journal': '科学通报'})['tier'], 'top')
        self.assertEqual(journal_tiers.classify({'journal_issn': '0023-074X'})['tier'], 'top')
        self.assertEqual(journal_tiers.classify({'journal_issn': '1674-733X'})['tier'], 'top')
        self.assertEqual(journal_tiers.classify({'journal_issn': '1674-7321'})['tier'], 'top')
        self.assertEqual(journal_tiers.classify({'journal_issn': '1674-7259'})['tier'], 'important')

    def test_mechanical_faculty_top_list_and_ijrr_official_title(self):
        for name in ('IJRR', 'International Journal of Robotics Research',
                     'The International Journal of Robotics Research', 'Int J Robot Res'):
            self.assertEqual(journal_tiers.classify({'journal': name})['tier'], 'top')
        for name in ('Journal of Field Robotics', 'Journal of Intelligent Manufacturing',
                     'Precision Engineering', 'International Journal of Production Research',
                     'Vehicle System Dynamics'):
            self.assertEqual(journal_tiers.classify({'journal': name})['tier'], 'top')
        self.assertEqual(journal_tiers.classify({'journal_issn': '0278-3649'})['tier'], 'top')

    def test_mechanical_2019_explicit_list_is_covered(self):
        top = '''0278-3649 2168-0485 0267-9477 1524-9050 0888-3270 0146-9592
                 1094-4087 2327-9125 0094-114X 1552-3098 0022-460X 0301-679X
                 0043-1648 1050-0472 0020-7403 0278-6125 1556-4959 0018-9294
                 0963-8695 0956-5515 1087-1357 1474-0346 0961-5539 0141-6359
                 0020-7543 0042-3114'''.split()
        # Two 2019 important journals (0023-074X and 1674-733X) are 2025 Top.
        important = '0954-4070 1674-7259 0577-6686 1000-680X'.split()
        self.assertEqual(len(top), 26)
        for issn in top:
            with self.subTest(issn=issn):
                self.assertEqual(journal_tiers.classify({'journal_issn': issn})['tier'], 'top')
        for issn in important:
            with self.subTest(issn=issn):
                self.assertEqual(journal_tiers.classify({'journal_issn': issn})['tier'], 'important')
        for issn in ('0023-074X', '1674-733X'):
            with self.subTest(issn=issn):
                self.assertEqual(journal_tiers.classify({'journal_issn': issn})['tier'], 'top')

    def test_imported_citation_suffix_and_abbreviation(self):
        for name in ('Advanced Intelligent Systems 2024.6:2400022',
                     'Adv. Intell. Syst.', 'Advanced Intelligent Systems'):
            with self.subTest(name=name):
                self.assertEqual(journal_tiers.classify({'journal': name})['tier'], 'important')
        self.assertEqual(journal_tiers.classify({'journal': 'Advanced Intelligent Systems 2024.6:2400022',
                                                 'journal_abbr': 'Adv. Intell. Syst.'})['tier'], 'important')

    def test_conflicting_journal_identifiers_fail_closed(self):
        result = journal_tiers.classify({'journal': 'Nature Communications',
                                         'journal_issn': '0278-3649'})
        self.assertEqual(result['tier'], 'other')
        self.assertTrue(result['tier_needs_review'])
        result = journal_tiers.classify({'journal': 'Nature Communications',
                                         'journal_abbr': 'IJRR'})
        self.assertEqual(result['tier'], 'other')
        self.assertTrue(result['tier_needs_review'])

    def test_duplicate_issn_fails_at_catalogue_load(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'tiers.csv'
            path.write_text('tier,name,issn,aliases,basis,source_url\n'
                            'top,First,0278-3649,,test,https://example.org/first\n'
                            'important,Second,0278-3649,,test,https://example.org/second\n',
                            encoding='utf-8')
            with patch.object(journal_tiers, 'CATALOGUE', path):
                journal_tiers._catalogue.cache_clear()
                with self.assertRaisesRegex(ValueError, 'ISSN 重复'):
                    journal_tiers._catalogue()
        journal_tiers._catalogue.cache_clear()

    def test_paper_type_overrides_journal_name(self):
        self.assertEqual(journal_tiers.classify({'paper_type': 'conference', 'journal': 'Nature Communications'})['tier'], 'conference')
        self.assertEqual(journal_tiers.classify({'paper_type': 'preprint', 'journal': 'Nature Communications'})['tier'], 'preprint')


if __name__ == '__main__':
    unittest.main()
