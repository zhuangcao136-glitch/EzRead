"""SQLite connection, documents and settings persistence."""
from __future__ import annotations
from .context import ApplicationContext
from .preferences import THEMES
import copy
import contextlib
import json
import sqlite3


def init_db(app: ApplicationContext):
    app.LIBRARY.mkdir(parents=True, exist_ok=True)
    (app.DATA / 'tmp').mkdir(exist_ok=True)
    with contextlib.closing(sqlite3.connect(app.DATA / 'library.sqlite3')) as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS papers (id TEXT PRIMARY KEY, hash TEXT UNIQUE, doc TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)')
        con.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        import paper_ai
        paper_ai.init(con)
        con.commit()
    for doc in app.all_docs(include_deleted=True):
        changed = False
        for key, value in {'paper_type': 'journal', 'conference_name': '', 'conference_abbr': '',
                           'conference_track': 'unknown'}.items():
            if key not in doc:
                doc[key] = value
                changed = True
        if doc.get('translation', {}).get('status') in ('running', 'queued'):
            doc['translation']['status'] = 'paused'
            doc['translation']['error'] = '上次任务已保存，可继续翻译。'
            changed = True
        for kind in ('summarize', 'team'):
            if doc.get(kind + '_status') in ('running', 'queued'):
                doc[kind + '_status'] = 'error'
                doc[kind + '_error'] = '上次任务未完成，可重新启动。'
                changed = True
        if changed:
            app.put_doc(doc)


@contextlib.contextmanager
def db(app: ApplicationContext):
    con = sqlite3.connect(app.DATA / 'library.sqlite3', timeout=30)
    try:
        with con:
            yield con
    finally:
        con.close()


def all_docs(app: ApplicationContext, include_deleted=False):
    with app.LOCK, app.db() as con:
        rows = con.execute('SELECT doc FROM papers' + ('' if include_deleted else ' WHERE deleted=0')).fetchall()
        return [json.loads(r[0]) for r in rows]


def get_doc(app: ApplicationContext, pid, include_deleted=False):
    with app.LOCK, app.db() as con:
        row = con.execute('SELECT doc FROM papers WHERE id=?' + ('' if include_deleted else ' AND deleted=0'), (pid,)).fetchone()
        if not row:
            raise ValueError('这篇文献不存在或已在回收站中。')
        return json.loads(row[0])


def put_doc(app: ApplicationContext, doc):
    with app.LOCK, app.db() as con:
        con.execute('INSERT INTO papers(id,hash,doc,deleted) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET doc=excluded.doc,deleted=excluded.deleted',
                    (doc['id'], doc['hash'], json.dumps(doc, ensure_ascii=False), int(doc.get('deleted', False))))


def update_doc(app: ApplicationContext, pid, fn):
    with app.LOCK:
        doc = app.get_doc(pid, include_deleted=True)
        fn(doc)
        doc['updated_at'] = app.now()
        app.put_doc(doc)
        return doc


def settings(app: ApplicationContext):
    out = copy.deepcopy(app.DEFAULT_SETTINGS)
    with app.LOCK, app.db() as con:
        for k, v in con.execute('SELECT key,value FROM settings'):
            if k in app.DEFAULT_SETTINGS:
                out[k] = json.loads(v)
    if out.get('theme') not in THEMES:
        out['theme'] = app.DEFAULT_SETTINGS['theme']
    return out
