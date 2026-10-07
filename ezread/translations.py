"""Translation snapshots, drafts, configuration and staged commit."""
from __future__ import annotations
from .context import ApplicationContext
import copy
import secrets
from .text_actions import original_text, digest


def version_metadata(version):
    return {k: copy.deepcopy(version.get(k)) for k in ('id', 'created_at', 'config', 'mixed', 'block_count', 'incomplete')}


def translation_snapshot(app: ApplicationContext, doc):
    current = doc.get('translation_current', {})
    blocks = {b['id']: {k: copy.deepcopy(b[k]) for k in app.TRANSLATION_FIELDS if k in b} for b in doc.get('blocks', [])}
    return {'id': secrets.token_hex(8), 'created_at': app.now(), 'config': copy.deepcopy(current.get('config')),
            'mixed': current.get('mixed', bool(any(b.get('translation') for b in doc.get('blocks', [])) and not current)),
            'block_count': sum(bool(v.get('translation', '').strip()) for v in blocks.values()), 'blocks': blocks}


def archive_translation(app: ApplicationContext, doc):
    snapshot = app.translation_snapshot(doc)
    if snapshot['block_count']:
        doc.setdefault('translation_versions', []).append(snapshot)


def archive_translation_draft(app: ApplicationContext, doc):
    task = doc.get('translation', {})
    if isinstance(task.get('staging'), dict):
        doc.setdefault('translation_drafts', []).append({
            'id': secrets.token_hex(8), 'created_at': app.now(), 'config': copy.deepcopy(task.get('config')),
            'block_count': len(task['staging']), 'incomplete': True, 'task': copy.deepcopy(task)})


def restore_translation_draft(app: ApplicationContext, pid, draft_id):
    import codex_models
    with app.LOCK:
        if (pid, 'translate') in app.QUEUED:
            raise ValueError('请先暂停翻译并等待当前批次停止，再恢复重译草稿。')
        doc = app.get_doc(pid)
        draft = next((v for v in doc.get('translation_drafts', []) if v['id'] == draft_id), None)
        if not draft:
            raise ValueError('重译草稿不存在。')
        config = draft['task']['config']
        codex_models.resolve_config(config['model'], config['reasoning_effort'])
        app.archive_translation_draft(doc)
        doc['translation'] = copy.deepcopy(draft['task'])
        doc['translation'].update(status='paused', error='已恢复重译草稿，可继续翻译。')
        doc['updated_at'] = app.now()
        app.put_doc(doc)
        return doc


def restore_translation(app: ApplicationContext, pid, version_id):
    with app.LOCK:
        if (pid, 'translate') in app.QUEUED:
            raise ValueError('请先暂停翻译并等待当前批次停止，再恢复译文版本。')
        doc = app.get_doc(pid)
        version = next((v for v in doc.get('translation_versions', []) if v['id'] == version_id), None)
        if not version:
            raise ValueError('译文版本不存在。')
        app.archive_translation_draft(doc)
        app.archive_translation(doc)
        for block in doc.get('blocks', []):
            saved = version['blocks'].get(block['id'], {})
            for key in app.TRANSLATION_FIELDS:
                block.pop(key, None)
            block.update(copy.deepcopy(saved))
        doc['translation_current'] = {'config': copy.deepcopy(version.get('config')), 'mixed': version.get('mixed', False), 'restored_from': version_id, 'created_at': app.now()}
        config = copy.deepcopy(version.get('config'))
        doc['translation'] = {'status': 'paused', 'error': '', 'mode': 'translate', 'config': config}
        counts = app.translation_counts(doc)
        doc['translation']['status'] = 'completed' if counts['done'] == counts['total'] else 'paused'
        doc['updated_at'] = app.now()
        app.put_doc(doc)
        return doc


def prepare_translation(app: ApplicationContext, doc, options=None):
    """Freeze explicit parameters before queueing; never resolve defaults in a worker."""
    import codex_models
    options = options or {}
    if not isinstance(options, dict) or set(options) - {'model', 'reasoning_effort', 'retranslate'}:
        raise ValueError('翻译配置包含未知字段。')
    if 'retranslate' in options and not isinstance(options['retranslate'], bool):
        raise ValueError('重新翻译选项必须是布尔值。')
    for key in ('model', 'reasoning_effort'):
        if key in options and (not isinstance(options[key], str) or len(options[key]) > 160):
            raise ValueError('模型与推理强度必须是有效选项。')
    task = doc.setdefault('translation', {})
    existing = task.get('config')
    retranslate = options.get('retranslate', False)
    explicit_config = 'model' in options or 'reasoning_effort' in options
    import paper_ai
    with app.db() as con:
        paper_model = paper_ai.current(con, doc['id'])
    if existing and not retranslate:
        config = codex_models.resolve_config(existing['model'], existing['reasoning_effort'])
        if explicit_config:
            requested = codex_models.resolve_config(options.get('model', existing['model']), options.get('reasoning_effort', existing['reasoning_effort']))
            if requested != config:
                raise ValueError('此任务已固定模型配置。更换配置请明确选择“用新配置重新翻译”。')
        if task.get('run_id'):
            return
    else:
        defaults = app.settings()
        config = codex_models.resolve_config(options.get('model', paper_model['model'] or defaults['translation_model']),
                                             options.get('reasoning_effort', paper_model['reasoning_effort'] or defaults['translation_reasoning_effort']))
        with app.db() as con:
            config = paper_ai.lock_model(con, doc['id'], config['model'], config['reasoning_effort'])
    if existing and not retranslate and not paper_model['model']:
        with app.db() as con:
            paper_ai.lock_model(con, doc['id'], config['model'], config['reasoning_effort'])
    if isinstance(task.get('staging'), dict):
        if not retranslate or config == existing:
            return
        app.archive_translation_draft(doc)
    run_id = secrets.token_hex(8)
    task.update(config=copy.deepcopy(config), run_id=run_id, mode='retranslate' if retranslate else 'translate', created_at=app.now())
    task['thread_id'] = None
    doc.setdefault('translation_runs', {})[run_id] = {'config': copy.deepcopy(config), 'created_at': task['created_at']}
    if retranslate:
        task['staging'] = {}


def execute_translation(app: ApplicationContext, pid, event):
    import paper_ai
    doc = app.get_doc(pid)
    task = doc['translation']
    config, run_id = copy.deepcopy(task['config']), task['run_id']
    staging = task.get('staging')
    targets = [b for b in doc.get('blocks', []) if b.get('kind') != 'reference' and not b.get('is_formula') and original_text(b).strip()]
    staged_hashes = task.get('staging_source_hashes', {})
    if isinstance(staging, dict):
        pending = [b for b in targets if not staging.get(b['id'], '').strip() or
                   staged_hashes.get(b['id'], digest(b.get('text', ''))) != digest(original_text(b))]
    else:
        pending = [b for b in targets if not b.get('translation', '').strip() and not b.get('translation_manually_edited')]
    app.update_doc(pid, lambda d: d['translation'].update(status='running', error=''))
    for batch in app.batches(pending):
        if event.is_set():
            break
        def remember_thread(tid):
            task['thread_id'] = tid
            app.update_doc(pid, lambda d: d['translation'].update(thread_id=tid)
                       if d['translation'].get('run_id') == run_id else None)
        thread_id, result = paper_ai.translate_batch(app.db, pid, doc, batch, config,
                                                     task.get('thread_id'),
                                                     on_thread=remember_thread,cancel_event=event)
        if not isinstance(result, dict) or set(result) != {b['id'] for b in batch} or any(not isinstance(result.get(b['id']), str) or not result[b['id']].strip() for b in batch):
            raise ValueError('模型未完整返回该批所有段落；已保留之前的译文，请重试。')
        def merge(d):
            active = d['translation']
            if active.get('run_id') != run_id:
                raise ValueError('翻译任务已变更，本批结果未覆盖新任务。')
            active['thread_id'] = thread_id
            if isinstance(active.get('staging'), dict):
                active['staging'].update(result)
                active.setdefault('staging_source_hashes', {}).update({b['id']: digest(original_text(b)) for b in batch})
            else:
                for block in d['blocks']:
                    if block['id'] in result and not block.get('translation', '').strip() and not block.get('translation_manually_edited'):
                        block.update(translation=result[block['id']], translation_run_id=run_id, translation_manually_edited=False,
                                     translation_source_hash=digest(original_text(next(b for b in batch if b['id'] == block['id']))))
        app.update_doc(pid, merge)
        task['thread_id'] = thread_id
    def finish(d):
        active = d['translation']
        if active.get('run_id') != run_id:
            raise ValueError('翻译任务已变更。')
        if event.is_set():
            active.update(status='paused', error='')
            return
        if isinstance(active.get('staging'), dict):
            if any(not active['staging'].get(b['id'], '').strip() for b in targets):
                raise ValueError('重译尚未完整，继续保留当前可读版本。')
            current_by_id = {b['id']: b for b in d['blocks']}
            for expected in targets:
                current = current_by_id[expected['id']]
                source_hash = active.get('staging_source_hashes', {}).get(expected['id'], digest(expected.get('text', '')))
                if current.get('source_range_revision') != expected.get('source_range_revision') or digest(original_text(current)) != source_hash:
                    raise ValueError('翻译期间正文有新修订，重译结果保留为草稿，未覆盖新内容。')
            app.archive_translation(d)
            for block in d['blocks']:
                if block['id'] in active['staging']:
                    for key in app.TRANSLATION_FIELDS:
                        block.pop(key, None)
                    block.update(translation=active['staging'][block['id']], translation_run_id=run_id, translation_manually_edited=False,
                                 translation_source_hash=active.get('staging_source_hashes', {}).get(block['id'], digest(next((original_text(b) for b in targets if b['id'] == block['id']), block.get('text', '')))))
            del active['staging']
            active.pop('staging_source_hashes', None)
        translated = [b for b in d['blocks'] if b.get('kind') != 'reference' and not b.get('is_formula') and b.get('text', '').strip()]
        sources = [d.get('translation_runs', {}).get(b.get('translation_run_id'), {}).get('config') for b in translated]
        homogeneous = bool(sources) and all(source is not None and source == sources[0] for source in sources)
        d['translation_current'] = {'config': copy.deepcopy(sources[0]) if homogeneous else None, 'mixed': not homogeneous, 'created_at': app.now()}
        active.update(status='completed', error='')
    app.update_doc(pid, finish)
