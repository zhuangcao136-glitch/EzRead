"""EzRead composition entry point and compatibility API.

Feature logic lives in ezread/; keep startup and shared runtime state here.
"""
from __future__ import annotations
import argparse
import queue
import secrets
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from ezread.config import VERSION as VERSION, data_directory as _data_directory, listen_port
from ezread.selection_requests import SelectionRequests
from ezread.selection_session import SelectionSession
from ezread import (
    storage as _storage,
    preferences as _preferences,
    library as _library,
    imports as _imports,
    publication as _publication,
    structure as _structure,
    translations as _translations,
    tasks as _tasks,
    utils as _utils,
    web as _web,
    lifecycle as _lifecycle,
    processes as _processes,
)

ROOT = Path(__file__).resolve().parent
DATA = _data_directory(ROOT)
LIBRARY = DATA / 'library'
STATIC = ROOT / 'static'
PORT = 47831
LOCK = threading.RLock()
FILE_ACTION_LOCK = threading.Lock()
JOBS = queue.Queue()
CANCEL: dict[tuple[str, str], threading.Event] = {}
QUEUED: set[tuple[str, str]] = set()
RESUME_REQUESTED: set[tuple[str, str]] = set()
QUOTA_PAUSED = threading.Event()
STATUS_CACHE = {'at': 0, 'value': {}}
SHUTDOWN = threading.Event()
INSTANCE_ID = secrets.token_hex(16)
SELECTION_REQUESTS = SelectionRequests()
SELECTION_SESSION = SelectionSession()
STRUCTURE_JOBS = queue.Queue()
STRUCTURE_QUEUED = set()
DEFAULT_SETTINGS = {'sort': 'last_read', 'theme': 'paper', 'collections': [],
                    'ui_font_size': 16, 'reader_font_size': 18,
                    'reader_sync': True, 'reader_split_ratio': 0.5,
                    'translation_model': '', 'translation_reasoning_effort': '',
                    'selection_translation_model': 'gpt-6-luna', 'selection_translation_reasoning_effort': ''}
TRANSLATION_FIELDS = ('translation', 'translation_run_id', 'translation_manually_edited', 'translation_modified_at', 'translation_source_hash')

# Explicit context injection preserves the existing entry point used by scripts and tests.
_context = sys.modules[__name__]


def init_db():
    return _storage.init_db(_context)


def db():
    return _storage.db(_context)


def all_docs(include_deleted=False):
    return _storage.all_docs(_context, include_deleted)


def get_doc(pid, include_deleted=False):
    return _storage.get_doc(_context, pid, include_deleted)


def put_doc(doc):
    return _storage.put_doc(_context, doc)


def update_doc(pid, fn):
    return _storage.update_doc(_context, pid, fn)


def settings():
    return _storage.settings(_context)


valid_year = _preferences.valid_year


def validate_settings(data):
    return _preferences.validate_settings(_context, data)


valid_ratio = _preferences.valid_ratio


def validate_reader_state(value, doc):
    return _preferences.validate_reader_state(_context, value, doc)


def paper_file_action(pid, action):
    return _library.paper_file_action(_context, pid, action)


journal_abbr = _library.journal_abbr


media_url = _library.media_url


def translation_counts(doc):
    return _library.translation_counts(_context, doc)


def public_doc(doc, full=False):
    return _library.public_doc(_context, doc, full)


patch_paper_fields = _library.patch_paper_fields


def import_pdf(content, filename):
    return _imports.import_pdf(_context, content, filename)


def enrich_publication(pid, data):
    return _publication.enrich(_context, pid, data)


def schedule_structure(pid):
    return _structure.schedule_structure(_context, pid)


def commit_import_result(pid, original, result):
    return _structure.commit_import_result(_context, pid, original, result)


def structure_worker():
    return _structure.structure_worker(_context)


version_metadata = _translations.version_metadata


def translation_snapshot(doc):
    return _translations.translation_snapshot(_context, doc)


def archive_translation(doc):
    return _translations.archive_translation(_context, doc)


def archive_translation_draft(doc):
    return _translations.archive_translation_draft(_context, doc)


def restore_translation_draft(pid, draft_id):
    return _translations.restore_translation_draft(_context, pid, draft_id)


def restore_translation(pid, version_id):
    return _translations.restore_translation(_context, pid, version_id)


def prepare_translation(doc, options=None):
    return _translations.prepare_translation(_context, doc, options)


def execute_translation(pid, event):
    return _translations.execute_translation(_context, pid, event)


def codex_status(force=False):
    return _tasks.codex_status(_context, force)


def prepare_shutdown(data):
    return _lifecycle.prepare_shutdown(_context, data)


def attach_desktop(httpd, data):
    return _lifecycle.attach_desktop(_context, httpd, data)


def enqueue(pid, kind, options=None):
    return _tasks.enqueue(_context, pid, kind, options)


batches = _tasks.batches


def worker():
    return _tasks.worker(_context)


now = _utils.now


Handler = _web.handler_for(_context)


def main():
    global PORT
    _processes.attach_job()
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=listen_port())
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    PORT = args.port
    init_db()
    worker_thread = threading.Thread(target=worker, daemon=True, name='translation-queue')
    worker_thread.start()
    structure_thread = threading.Thread(target=structure_worker, daemon=True, name='paper-structure-queue')
    structure_thread.start()
    for doc in all_docs():
        if doc.get('reading_structure', {}).get('status') in ('queued', 'running'):
            schedule_structure(doc['id'])
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    except OSError:
        # Do not open an unknown listener or a different library after bind fails.
        raise
    if args.open:
        from desktop_runtime import open_desktop
        open_desktop(ROOT, DATA, f'http://127.0.0.1:{PORT}')
    print(f'EzRead {VERSION} ready: http://127.0.0.1:{PORT}', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        import codex_bridge
        import codex_usage
        import codex_models
        import selection_codex
        SHUTDOWN.set()
        _lifecycle.pause_jobs(_context)
        JOBS.put(None)
        STRUCTURE_JOBS.put(None)
        _processes.shutdown(codex_bridge._stop)
        SELECTION_REQUESTS.shutdown()
        selection_codex.shutdown()
        codex_usage.shutdown()
        codex_models.shutdown()
        for event in CANCEL.values():
            event.set()
        worker_thread.join(timeout=5)
        structure_thread.join(timeout=2)
        httpd.server_close()


if __name__ == '__main__':
    main()
