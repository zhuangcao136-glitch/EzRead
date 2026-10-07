"""Read-only, offline English-Chinese lookup in the bundled ECDICT database."""
from pathlib import Path
import contextlib
import re
import sqlite3
import unicodedata

DATABASE = Path(__file__).resolve().parents[1] / 'assets/dictionaries/ecdict.sqlite3'
SCHEMA_VERSION = '1'


def normalize(value):
    value = unicodedata.normalize('NFKC', value).strip()
    value = value.translate(str.maketrans({'’': "'", '‘': "'", '‐': '-', '‑': '-', '–': '-'}))
    return ' '.join(value.casefold().split()).strip('.,;:!?"“”()[]{}，。；：！？')


def lookup(text, database=None):
    key = normalize(text)
    if not key or len(key) > 160 or not re.search(r'[a-z]', key):
        return missing('请在英文原文中选择一个单词或较短词组；完整句子请选择“翻译句子”。')
    path = Path(database) if database is not None else DATABASE
    if not path.is_file():
        raise ValueError('内置词典文件缺失，请恢复 assets/dictionaries/ecdict.sqlite3。')
    try:
        with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as con:
            con.row_factory = sqlite3.Row
            version = con.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if version is None or version[0] != SCHEMA_VERSION:
                raise ValueError('内置词典版本不兼容，请重新构建词典。')
            rows = con.execute('SELECT * FROM entries WHERE key=?', (key,)).fetchall()
            match = 'exact'
            if not rows:
                rows = con.execute('''SELECT e.* FROM forms f JOIN entries e ON e.key=f.word
                    WHERE f.form=? ORDER BY e.key LIMIT 5''', (key,)).fetchall()
                match = 'inflection'
    except sqlite3.Error as exc:
        raise ValueError('内置词典无法读取，请检查词典文件。') from exc
    entries = [dict(row) for row in rows]
    if not entries:
        return missing('离线词典未收录所选文字。请缩小选区，或选择“翻译句子”获取语境译文。')
    parts = []
    for entry in entries:
        heading = entry['word'] + (f" /{entry['phonetic']}/" if entry['phonetic'] else '')
        if match == 'inflection':
            heading += '（原形）'
        parts.append(heading + '\n' + entry['translation'])
    return {'translation': '\n\n'.join(parts), 'entries': entries, 'found': True,
            'match': match, 'source': 'ECDICT', 'method': 'dictionary', 'target_language': 'zh'}


def missing(message):
    return {'translation': message, 'entries': [], 'found': False, 'match': None,
            'source': 'ECDICT', 'method': 'dictionary', 'target_language': 'zh'}
