"""Real bundled dictionary checks; no network or model access."""
import contextlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ezread import dictionary


class DictionaryTests(unittest.TestCase):
    def test_bundled_dictionary_contains_reading_and_scientific_terms(self):
        for text in ('sensor', 'tactile', 'compliance', 'Finite Element', 'robots', '“sensor,”'):
            with self.subTest(text=text):
                result = dictionary.lookup(text)
                self.assertTrue(result['found'])
                self.assertTrue(result['entries'][0]['translation'])
                self.assertEqual(result['method'], 'dictionary')

    def test_miss_is_explicit_and_does_not_fabricate_a_translation(self):
        for value in ('xyznotarealwordxyz', '只有中文', 'sensor ' * 80):
            result = dictionary.lookup(value)
            self.assertFalse(result['found'])
            self.assertEqual(result['entries'], [])

    def test_inflected_form_resolves_to_lemma_without_overriding_exact_match(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work') as folder:
            path = Path(folder) / 'test.sqlite3'
            with contextlib.closing(sqlite3.connect(path)) as con:
                con.execute('CREATE TABLE metadata(key,value)')
                con.execute("INSERT INTO metadata VALUES('schema_version','1')")
                con.execute('CREATE TABLE entries(key,word,phonetic,translation,pos,exchange)')
                con.execute("INSERT INTO entries VALUES('measure','measure','','测量','','')")
                con.execute('CREATE TABLE forms(form,word)')
                con.execute("INSERT INTO forms VALUES('measured','measure')")
                con.commit()
            self.assertEqual(dictionary.lookup('measured', path)['match'], 'inflection')
            self.assertEqual(dictionary.lookup('measure', path)['match'], 'exact')
            self.assertFalse(dictionary.lookup("measure' OR 1=1 --", path)['found'])

    def test_database_provenance_and_integrity(self):
        source = json.loads(dictionary.DATABASE.with_name('source.json').read_text(encoding='utf-8'))
        self.assertEqual(dictionary.DATABASE.stat().st_size, source['database_bytes'])
        with contextlib.closing(sqlite3.connect(dictionary.DATABASE.as_uri() + '?mode=ro', uri=True)) as con:
            self.assertEqual(con.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            self.assertEqual(con.execute('SELECT count(*) FROM entries').fetchone()[0], source['entries'])
        with self.assertRaises(ValueError):
            dictionary.lookup('sensor', Path('nonexistent-dictionary.sqlite3'))


if __name__ == '__main__':
    unittest.main()
