"""Background source structure checks; collection membership is user-controlled."""
from __future__ import annotations
from .context import ApplicationContext
import copy
import json


def schedule_structure(app: ApplicationContext, pid):
    """One bounded semantic check after import; it never blocks PDF ingestion."""
    import paper_structure
    with app.LOCK:
        if app.SHUTDOWN.is_set():
            raise ValueError('应用正在关闭，结构校对未加入队列。')
        doc = app.get_doc(pid)
        saved = doc.get('reading_structure', {})
        if pid in app.STRUCTURE_QUEUED or saved.get('status') == 'ready' and saved.get('version') == paper_structure.VERSION and saved.get('source_fingerprint') == paper_structure.fingerprint(doc):
            return
        if not doc.get('blocks'):
            return
        app.STRUCTURE_QUEUED.add(pid)
        requested_model = app.settings()['selection_translation_model']
        def mark_queued(d):
            d['reading_structure'] = {**paper_structure.organize(d), 'status': 'queued', 'requested_model': requested_model}
        app.update_doc(pid, mark_queued)
        app.STRUCTURE_JOBS.put(pid)


def commit_import_result(app: ApplicationContext, pid, original, result):
    """Commit source structure without changing any collection data."""
    import paper_structure
    result = copy.deepcopy(result)
    # Discard legacy classification output instead of applying or persisting it.
    result.pop('collection_plan', None)
    result.pop('collection_error', None)
    with app.LOCK, app.db() as con:
        if app.SHUTDOWN.is_set():
            raise ValueError('应用正在关闭，结构校对已停止。')
        doc = app.get_doc(pid)
        if paper_structure.fingerprint(doc) != original:
            raise ValueError('源文已变化，结构校对结果未覆盖当前内容。')
        doc['reading_structure'] = result
        doc['updated_at'] = app.now()
        con.execute('UPDATE papers SET doc=? WHERE id=? AND deleted=0', (json.dumps(doc, ensure_ascii=False), pid))
    return doc


def structure_worker(app: ApplicationContext):
    import paper_structure, codex_models
    while True:
        pid = app.STRUCTURE_JOBS.get()
        if pid is None:
            app.STRUCTURE_JOBS.task_done()
            return
        try:
            if app.SHUTDOWN.is_set():
                continue
            doc = app.get_doc(pid)
            original = paper_structure.fingerprint(doc)
            def mark_running(d):
                d['reading_structure'].update(status='running', error='')
            app.update_doc(pid, mark_running)
            config = codex_models.resolve_config(doc['reading_structure']['requested_model'])
            result = paper_structure.refine(doc, config)
            app.commit_import_result(pid, original, result)
        except Exception as exc:
            error = str(exc)[:500]
            try:
                def mark_failed(d):
                    d['reading_structure'].update(status='needs_review', error=error)
                app.update_doc(pid, mark_failed)
            except (ValueError, KeyError):
                pass
        finally:
            with app.LOCK:
                app.STRUCTURE_QUEUED.discard(pid)
            app.STRUCTURE_JOBS.task_done()
