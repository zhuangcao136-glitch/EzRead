"""Build the bundled offline dictionary from a local, pinned ECDICT CSV.

Downloads are deliberately separate. Verify upstream/license and source_sha256
in assets/dictionaries/source.json before invoking this script.
"""
import argparse
import contextlib
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ezread.dictionary import normalize, SCHEMA_VERSION


def build(source, destination, provenance=None):
    source, destination = Path(source), Path(destination)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if provenance and provenance.get('source_sha256') != digest:
        raise ValueError('ECDICT source checksum does not match source.json.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dictionary-build-', dir=destination.parent) as folder:
        temporary = Path(folder) / 'ecdict.sqlite3'
        with contextlib.closing(sqlite3.connect(temporary)) as con:
            con.execute('PRAGMA journal_mode=OFF')
            con.execute('PRAGMA synchronous=OFF')
            con.execute('''CREATE TABLE entries (key TEXT PRIMARY KEY, word TEXT NOT NULL,
                phonetic TEXT NOT NULL, translation TEXT NOT NULL, pos TEXT NOT NULL,
                exchange TEXT NOT NULL) WITHOUT ROWID''')
            con.execute('''CREATE TABLE forms (form TEXT NOT NULL, word TEXT NOT NULL,
                PRIMARY KEY(form,word)) WITHOUT ROWID''')
            con.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID')
            rejected = 0
            with source.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                if not {'word', 'translation', 'phonetic', 'exchange'} <= set(reader.fieldnames or []):
                    raise ValueError('Invalid ECDICT CSV fields.')
                for row in reader:
                    word = row.get('word') or ''
                    key = normalize(word)
                    translation = (row.get('translation') or '').replace('\\n', '\n').strip()
                    if not key or len(key) > 160 or not translation:
                        rejected += 1
                        continue
                    exchange = row.get('exchange') or ''
                    con.execute('INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?)',
                                (key, word, row.get('phonetic') or '', translation, row.get('pos') or '', exchange))
                    for change in exchange.split('/'):
                        kind, _, form = change.partition(':')
                        form = normalize(form)
                        if kind in {'p', 'd', 'i', '3', 'r', 't', 's'} and form and form != key and len(form) <= 160:
                            con.execute('INSERT OR IGNORE INTO forms VALUES(?,?)', (form, key))
            con.execute('DELETE FROM forms WHERE word NOT IN (SELECT key FROM entries)')
            count = con.execute('SELECT count(*) FROM entries').fetchone()[0]
            forms = con.execute('SELECT count(*) FROM forms').fetchone()[0]
            con.executemany('INSERT INTO metadata VALUES(?,?)', [
                ('schema_version', SCHEMA_VERSION), ('source_sha256', digest),
                ('entries', str(count)), ('forms', str(forms))])
            con.commit()
            con.execute('VACUUM')
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Dictionary integrity check failed.')
        temporary.replace(destination)
    return {'entries': count, 'forms': forms, 'excluded_rows': rejected,
            'database_bytes': destination.stat().st_size,
            'database_sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'assets/dictionaries/ecdict.sqlite3')
    parser.add_argument('--provenance', type=Path, default=ROOT / 'assets/dictionaries/source.json')
    args = parser.parse_args()
    metadata = json.loads(args.provenance.read_text(encoding='utf-8'))
    result = build(args.source, args.output, metadata)
    metadata.update(result)
    args.provenance.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
