"""Background structure and topic classification with atomic commit."""
from __future__ import annotations
from .context import ApplicationContext
import copy
import json


def schedule_structure(app: ApplicationContext, pid):
    """One bounded semantic check after import; it never blocks PDF ingestion."""
    import paper_structure
    with app.LOCK:
        doc = app.get_doc(pid)
        saved = doc.get('reading_structure', {})
        classify = doc.get('collection_assignment', {}).get('status') in ('queued', 'running', 'needs_review')
        if pid in app.STRUCTURE_QUEUED or not classify and saved.get('status') == 'ready' and saved.get('version') == paper_structure.VERSION and saved.get('source_fingerprint') == paper_structure.fingerprint(doc):
            return
        if not doc.get('blocks'):
            return
        app.STRUCTURE_QUEUED.add(pid)
        requested_model = app.settings()['selection_translation_model']
        def mark_queued(d):
            d['reading_structure'] = {**paper_structure.organize(d), 'status': 'queued', 'requested_model': requested_model}
            if classify:
                d['collection_assignment'].update(status='queued', error='')
        app.update_doc(pid, mark_queued)
        app.STRUCTURE_JOBS.put(pid)


def import_collection_context(app: ApplicationContext):
    import paper_structure
    docs = app.all_docs()
    names = set(app.settings().get('collections', [])) | {d['collection'] for d in docs if d.get('collection')}
    return paper_structure.collection_context(names, docs)


def commit_import_result(app: ApplicationContext, pid, original, result):
    """Commit the current paper and any newly created collection atomically."""
    import paper_structure
    result = copy.deepcopy(result)
    plan, error = result.pop('collection_plan', None), result.pop('collection_error', '')
    with app.LOCK, app.db() as con:
        doc = app.get_doc(pid)
        if paper_structure.fingerprint(doc) != original:
            raise ValueError('源文已变化，结构校对结果未覆盖当前内容。')
        doc['reading_structure'] = result
        assignment = doc.get('collection_assignment', {})
        if assignment.get('status') in ('queued', 'running', 'needs_review'):
            if plan:
                # A manual creation or another import may have added the same name meanwhile.
                plan = paper_structure.validate_collection(app.import_collection_context(), plan)
                doc['collection'] = plan['name']
                doc['collection_assignment'] = {**plan, 'status': 'needs_review' if plan['mode'] == 'uncertain' else 'ready',
                    'error': plan['reason'] if plan['mode'] == 'uncertain' else '', 'model_config': result.get('model_config')}
                if plan['mode'] != 'uncertain':
                    names = app.settings().get('collections', [])
                    names = list(dict.fromkeys([*names, plan['name']]))
                    con.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                                ('collections', json.dumps(names, ensure_ascii=False)))
            else:
                assignment.update(status='needs_review', error=error or '主题归类结果缺失，请重试或手动移入合集。')
        doc['updated_at'] = app.now()
        con.execute('UPDATE papers SET doc=? WHERE id=? AND deleted=0', (json.dumps(doc, ensure_ascii=False), pid))
    return doc


def structure_worker(app: ApplicationContext):
    import paper_structure, codex_models
    while True:
        pid = app.STRUCTURE_JOBS.get()
        try:
            doc = app.get_doc(pid)
            original = paper_structure.fingerprint(doc)
            classify = doc.get('collection_assignment', {}).get('status') in ('queued', 'running', 'needs_review')
            def mark_running(d):
                d['reading_structure'].update(status='running', error='')
                if classify and d.get('collection_assignment', {}).get('status') != 'manual':
                    d['collection_assignment'].update(status='running', error='')
            app.update_doc(pid, mark_running)
            config = codex_models.resolve_config(doc['reading_structure']['requested_model'])
            kwargs = {'collection_context': app.import_collection_context()} if classify else {}
            result = paper_structure.refine(doc, config, **kwargs)
            app.commit_import_result(pid, original, result)
        except Exception as exc:
            try:
                def mark_failed(d):
                    d['reading_structure'].update(status='needs_review', error=str(exc)[:500])
                    if d.get('collection_assignment', {}).get('status') in ('queued', 'running', 'needs_review'):
                        d['collection_assignment'].update(status='needs_review', error=str(exc)[:500])
                app.update_doc(pid, mark_failed)
            except (ValueError, KeyError):
                pass
        finally:
            with app.LOCK:
                app.STRUCTURE_QUEUED.discard(pid)
            app.STRUCTURE_JOBS.task_done()
