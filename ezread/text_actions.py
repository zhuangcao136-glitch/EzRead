"""Character-range annotations and reversible source/translation corrections.

Offsets are Unicode code points, not browser UTF-16 units. Original PDF/extracted
text and source block IDs stay intact. Resolve uncertain anchors conservatively.
"""
from __future__ import annotations
import copy
import hashlib
import secrets


class TextConflict(ValueError):
    pass


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def original_text(block):
    return block.get('text_override', block.get('text', ''))


def text_of(block, language):
    if language == 'original': return original_text(block)
    if language == 'translation': return block.get('translation', '')
    raise ValueError('无效的文字语言。')


def model_document(doc):
    result = copy.deepcopy(doc)
    for block in result.get('blocks', []): block['text'] = original_text(block)
    return result


def review_needed(block):
    return bool(block.get('translation')) and digest(original_text(block)) != block.get('translation_source_hash', digest(block.get('text', '')))


def validate_ranges(doc, ranges, limit=20000):
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= 200:
        raise ValueError('请先选择正文中的文字。')
    by_id = {b['id']: b for b in doc.get('blocks', [])}
    result, seen = [], set()
    for value in ranges:
        if not isinstance(value, dict): raise ValueError('文字范围无效。')
        bid, language = value.get('block_id'), value.get('language')
        if language not in ('original', 'translation'): raise ValueError('无效的文字语言。')
        if not isinstance(bid, str) or (bid, language) in seen: raise ValueError('文字范围重复或无效。')
        block = by_id.get(bid)
        if not block or block.get('is_formula'): raise ValueError('所选正文不存在。')
        text = text_of(block, language)
        start, end, quote = value.get('start'), value.get('end'), value.get('quote')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise TextConflict('正文已变化，请重新选择文字。')
        if not isinstance(quote, str) or text[start:end] != quote or value.get('base_text') != text:
            raise TextConflict('正文已变化，未覆盖新内容；请重新选择文字。')
        seen.add((bid, language))
        result.append({'block_id': bid, 'language': language, 'start': start, 'end': end,
                       'quote': quote, 'prefix': text[max(0, start - 24):start],
                       'suffix': text[end:end + 24], 'text_hash': digest(text)})
    if sum(len(r['quote']) for r in result) > limit: raise ValueError(f'每次最多选择 {limit} 个字符。')
    return result


def resolve_range(block, anchor):
    if not block: return {**anchor, 'status': 'needs_review'}
    text = text_of(block, anchor['language'])
    start, end, quote = anchor['start'], anchor['end'], anchor['quote']
    if anchor['text_hash'] == digest(text) and text[start:end] == quote:
        return {**anchor, 'status': 'resolved'}
    positions, cursor = [], 0
    while quote and (cursor := text.find(quote, cursor)) >= 0:
        positions.append(cursor); cursor += 1
        if len(positions) > 1000: return {**anchor, 'status': 'needs_review'}
    if len(positions) > 1:
        positions = [p for p in positions if (not anchor['prefix'] or text[:p].endswith(anchor['prefix'])) and
                     (not anchor['suffix'] or text[p + len(quote):].startswith(anchor['suffix']))]
    if len(positions) != 1: return {**anchor, 'status': 'needs_review'}
    return {**anchor, 'start': positions[0], 'end': positions[0] + len(quote), 'status': 'resolved'}


def resolved_annotations(doc):
    by_id = {b['id']: b for b in doc.get('blocks', [])}
    result = []
    for item in doc.get('text_annotations', []):
        ranges = [resolve_range(by_id.get(r['block_id']), r) for r in item['ranges']]
        result.append({**item, 'ranges': ranges,
                       'status': 'resolved' if all(r['status'] == 'resolved' for r in ranges) else 'needs_review'})
    return result


def _record_revision(doc, changes, timestamp, restored_from=None):
    doc.setdefault('text_revision_history', []).append({'id': secrets.token_hex(8), 'created_at': timestamp,
         'changes': changes, 'restored_from': restored_from})


def _set_text(block, language, value, timestamp):
    block['source_range_revision'] = secrets.token_hex(8)
    if language == 'original': block.update(text_override=value, text_modified_at=timestamp)
    else: block.update(translation=value, translation_manually_edited=True, translation_modified_at=timestamp)


def mutate(doc, data, timestamp):
    if doc.get('deleted'): raise ValueError('文献已移入回收站。')
    action = data.get('action')
    if not isinstance(action, str): raise ValueError('文字操作无效。')
    if action in {'highlight', 'note', 'revise'}:
        ranges = validate_ranges(doc, data.get('ranges'))
        if action in {'highlight', 'note'}:
            note = data.get('note', '')
            if not isinstance(note, str) or len(note) > 20000 or action == 'note' and not note.strip():
                raise ValueError('请填写不超过 20000 字符的批注。')
            color = data.get('color', 'yellow')
            if action == 'highlight' and color not in ('yellow', 'red'):
                raise ValueError('请选择黄色或红色高亮。')
            annotation = {'id': secrets.token_hex(8), 'kind': action,
                'ranges': ranges, 'quote': '\n'.join(r['quote'] for r in ranges),
                'note': note, 'created_at': timestamp, 'modified_at': timestamp, 'revision': secrets.token_hex(8)}
            if action == 'highlight': annotation['color'] = color
            doc.setdefault('text_annotations', []).append(annotation)
            return
        replacement = data.get('replacement')
        if not isinstance(replacement, str) or len(replacement) > 60000: raise ValueError('修订内容过长或无效。')
        if len({r['language'] for r in ranges}) != 1: raise ValueError('请在同一种语言的正文中修订。')
        by_id = {b['id']: b for b in doc['blocks']}
        changes = []
        for index, anchor in enumerate(ranges):
            block = by_id[anchor['block_id']]
            before = text_of(block, anchor['language'])
            after = before[:anchor['start']] + (replacement if index == 0 else '') + before[anchor['end']:]
            changes.append({'block_id': block['id'], 'language': anchor['language'], 'before': before, 'after': after})
            _set_text(block, anchor['language'], after, timestamp)
        _record_revision(doc, changes, timestamp)
        return
    if action == 'restore_revision':
        record = next((v for v in doc.get('text_revision_history', []) if v['id'] == data.get('revision_id')), None)
        if not record: raise ValueError('修订记录不存在。')
        by_id = {b['id']: b for b in doc['blocks']}
        for change in record['changes']:
            if change['block_id'] not in by_id or text_of(by_id[change['block_id']], change['language']) != change['after']:
                raise TextConflict('这之后还有文字修改，请先撤回最近的修订，避免覆盖新内容。')
        changes = []
        for change in record['changes']:
            _set_text(by_id[change['block_id']], change['language'], change['before'], timestamp)
            changes.append({**change, 'before': change['after'], 'after': change['before']})
        _record_revision(doc, changes, timestamp, record['id'])
        return
    if action in {'edit_annotation', 'delete_annotation'}:
        item = next((a for a in doc.get('text_annotations', []) if a['id'] == data.get('annotation_id')), None)
        if not item: raise ValueError('标记不存在。')
        if data.get('modified_at') != item['modified_at']: raise TextConflict('批注已更新，请重新打开后编辑。')
        if data.get('revision') != item.get('revision'): raise TextConflict('批注已更新，请重新打开后编辑。')
        if action == 'edit_annotation' and data.get('previous_note') != item['note']: raise TextConflict('批注已更新，请重新打开后编辑。')
        if action == 'delete_annotation': doc['text_annotations'].remove(item)
        else:
            note = data.get('note')
            if not isinstance(note, str) or len(note) > 20000: raise ValueError('批注内容无效或过长。')
            item.update(note=note, modified_at=timestamp, revision=secrets.token_hex(8))
        return
    if action == 'edit_legacy_note':
        block = next((b for b in doc['blocks'] if b['id'] == data.get('block_id')), None)
        if not block: raise ValueError('段落不存在。')
        if data.get('previous_note') != block.get('note', ''): raise TextConflict('批注已更新，请重新打开后编辑。')
        if not isinstance(data.get('note'), str) or len(data['note']) > 20000: raise ValueError('批注内容无效或过长。')
        block['note'] = data['note']
        return
    raise ValueError('文字操作不存在。')


def apply(app, pid, data):
    # update_doc rereads under the shared lock, so validation and mutation are atomic.
    request_id = data.get('request_id')
    if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 64): raise ValueError('请求标识无效。')
    def update(doc):
        if doc.get('deleted'): raise ValueError('文献已移入回收站。')
        if request_id and request_id in doc.get('text_action_receipts', []): return
        if data.get('action') == 'revise' and isinstance(data.get('ranges'), list) and any(r.get('language') == 'translation' for r in data['ranges'] if isinstance(r, dict)):
            validate_ranges(doc, data.get('ranges'))
            app.archive_translation(doc)
        mutate(doc, data, app.now())
        doc['text_actions_revision'] = secrets.token_hex(8)
        if request_id: doc.setdefault('text_action_receipts', []).append(request_id)
    return app.update_doc(pid, update)


def selected_text(app, pid, data):
    doc = app.get_doc(pid)
    ranges = validate_ranges(doc, data.get('ranges'), limit=1200)
    selected = data.get('selected_text')
    if not isinstance(selected, str) or not selected.strip() or len(selected) > 1500:
        raise ValueError('每次请选择不超过 1200 字符的正文。')
    # The model input comes from validated source ranges, never arbitrary client text.
    return '\n'.join(r['quote'] for r in ranges), ranges


def translate(app, pid, data):
    import codex_models, codex_bridge
    from .selection_requests import ensure_current
    selected, ranges = selected_text(app, pid, data)
    event = app.SELECTION_REQUESTS.begin(pid, data)
    try:
        ensure_current(event)
        if not app.codex_status().get('authenticated'):
            raise ValueError('请先在官方 Codex 中登录 ChatGPT 账号。')
        settings = app.settings()
        config = codex_models.resolve_config(settings['selection_translation_model'], settings['selection_translation_reasoning_effort'])
        ensure_current(event)
        target = 'en' if all(r['language'] == 'translation' for r in ranges) else 'zh'
        result = codex_bridge.translate_selection(selected, **config, target_language=target, cancel_event=event)
        ensure_current(event)
        # A slow model response must not survive a revision to the source text.
        selected_text(app, pid, data)
        return {'translation': result, 'target_language': target, 'method': 'model'}
    finally:
        app.SELECTION_REQUESTS.finish(pid, data, event)


def translate_word(app, pid, data):
    from . import dictionary
    from .selection_requests import ensure_current
    selected, ranges = selected_text(app, pid, data)
    event = app.SELECTION_REQUESTS.begin(pid, data)
    try:
        ensure_current(event)
        result = dictionary.lookup(selected)
        ensure_current(event)
        return result
    finally:
        app.SELECTION_REQUESTS.finish(pid, data, event)


def retranslate(app, pid, data):
    import paper_ai, codex_models
    doc = app.get_doc(pid)
    ranges = validate_ranges(doc, data.get('ranges'))
    ids = [r['block_id'] for r in ranges]
    if (pid, 'translate') in app.QUEUED: raise ValueError('请先暂停全文翻译并等待当前批次结束。')
    source = model_document(doc)
    blocks = [b for b in source['blocks'] if b['id'] in ids and b['text'].strip() and not b.get('is_formula') and b.get('kind') != 'reference']
    if not blocks or sum(len(b['text']) for b in blocks) > 12000: raise ValueError('对应段落过长，请缩小选择范围。')
    if not app.codex_status().get('authenticated'): raise ValueError('请先在官方 Codex 中登录 ChatGPT 账号。')
    settings = app.settings()
    config = codex_models.resolve_config(settings['selection_translation_model'], settings['selection_translation_reasoning_effort'])
    _, result = paper_ai.translate_batch(app.db, pid, source, blocks, config, None)
    expected = {b['id']: (b['text'], b.get('translation', '')) for b in blocks}
    if not isinstance(result, dict) or set(result) != set(expected) or any(not isinstance(v, str) or not v.strip() for v in result.values()): raise ValueError('模型未完整返回译文，已有内容保持不变。')
    def merge(current):
        if current.get('deleted'): raise ValueError('文献已移入回收站，本次结果未覆盖内容。')
        if (pid, 'translate') in app.QUEUED: raise TextConflict('全文翻译已开始，本次结果未覆盖新任务。')
        by_id = {b['id']: b for b in current['blocks']}
        for bid, (english, old_translation) in expected.items():
            if original_text(by_id[bid]) != english or by_id[bid].get('translation', '') != old_translation:
                raise TextConflict('翻译期间文字有新修改，本次结果未覆盖新内容。')
        app.archive_translation(current)
        for bid, translation in result.items():
            by_id[bid].update(translation=translation, translation_manually_edited=False,
                             translation_source_hash=digest(expected[bid][0]), translation_modified_at=app.now(), source_range_revision=secrets.token_hex(8))
        current.setdefault('translation_current', {})['mixed'] = True
        current['text_actions_revision'] = secrets.token_hex(8)
    return app.update_doc(pid, merge)
