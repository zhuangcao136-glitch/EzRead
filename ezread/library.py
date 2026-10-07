"""Paper metadata, public documents and native file actions."""
from __future__ import annotations
from .context import ApplicationContext
import re
import shutil
import sqlite3
import urllib.parse


def paper_file_action(app: ApplicationContext, pid, action):
    """Resolve an active paper internally; clients cannot submit arbitrary paths."""
    if not re.fullmatch(r'[a-f0-9]{16}', pid) or action not in ('copy-pdf', 'reveal-pdf'):
        raise ValueError('无效的文件操作。')
    doc = app.get_doc(pid)
    source = (app.LIBRARY / pid / 'original.pdf').resolve()
    if not source.is_relative_to(app.LIBRARY.resolve()) or not source.is_file():
        raise ValueError('原 PDF 文件不存在。')
    import desktop_files
    if action == 'reveal-pdf':
        desktop_files.reveal_pdf(source)
        return {'ok': True}
    # Keep the imported filename when pasted, without renaming the library original.
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(doc.get('filename') or 'paper.pdf')).strip(' .')
    filename = filename[:150] or 'paper.pdf'
    if not filename.lower().endswith('.pdf'):
        filename += '.pdf'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', filename, re.I):
        filename = '_' + filename
    folder = app.DATA / 'clipboard' / pid
    folder.mkdir(parents=True, exist_ok=True)
    target = (folder / filename).resolve()
    if not target.is_relative_to((app.DATA / 'clipboard').resolve()):
        raise ValueError('无效的 PDF 文件名。')
    with app.FILE_ACTION_LOCK:
        shutil.copyfile(source, target)
        desktop_files.copy_pdf(target)
    return {'ok': True, 'filename': filename}


def journal_abbr(name):
    cleaned = re.sub(r'[^a-z0-9]', '', str(name).lower())
    aliases = {
        'ieeetransactionsonrobotics': 'TRO',
        'ieeeroboticsandautomationletters': 'RAL',
        'ieeeasmetransactionsonmechatronics': 'T-MECH',
        'naturecommunications': 'Nat. Commun.',
        'sciencerobotics': 'Sci. Robot.',
        'ieeetransactionsonautomation scienceandengineering'.replace(' ', ''): 'T-ASE',
        'ieeesensorsjournal': 'IEEE Sens. J.',
        'advancedintelligentsystems': 'Adv. Intell. Syst.',
        'softrobotics': 'Soft Robot.',
        'nature': 'Nature', 'science': 'Science',
    }
    return aliases.get(cleaned, name)


def media_url(pid, filename):
    return '/media/' + pid + '/' + urllib.parse.quote(str(filename).replace('\\', '/'), safe='/')


def translation_counts(app: ApplicationContext, doc):
    from .text_actions import original_text
    target = [b for b in doc.get('blocks', []) if b.get('kind') != 'reference' and not b.get('is_formula') and original_text(b).strip()]
    t = dict(doc.get('translation', {}))
    staging = t.pop('staging', None)
    current_done = sum(bool(b.get('translation', '').strip()) for b in target)
    done = sum(bool(staging.get(b['id'], '').strip()) for b in target) if isinstance(staging, dict) else current_done
    t.update(total=len(target), done=done, current_done=current_done)
    t['has_staging'] = isinstance(staging, dict)
    t['current_config'] = doc.get('translation_current', {}).get('config')
    t['current_mixed'] = doc.get('translation_current', {}).get('mixed', bool(current_done and not doc.get('translation_current')))
    t['versions'] = [app.version_metadata(v) for v in reversed(doc.get('translation_versions', []))]
    t['drafts'] = [app.version_metadata(v) for v in reversed(doc.get('translation_drafts', []))]
    t.setdefault('status', 'idle')
    t.setdefault('error', '')
    return t


def public_doc(app: ApplicationContext, doc, full=False):
    from . import journal_catalogue
    import paper_ai
    hidden = {'hash', 'blocks', 'pages', 'figures', 'translation_versions', 'translation_current',
              'translation_runs', 'translation_drafts', 'jif', 'jif_year', 'jif_source_url',
              'quartiles', 'metrics_source', 'metrics_sources', 'metrics_status', 'metrics_error',
              'metrics_note', 'conference_rankings', 'read_state', 'reading_structure', 'collection_assignment'}
    hidden.update({'text_annotations', 'text_revision_history', 'text_action_receipts'})
    hidden.update({'team', 'team_sources', 'team_status', 'team_error'})
    out = {k: v for k, v in doc.items() if k not in hidden}
    from .publication import assessment
    out['metadata_enrichment'] = assessment(doc)
    out.setdefault('paper_type', 'journal')
    out.setdefault('conference_name', '')
    out.setdefault('conference_abbr', '')
    out.setdefault('conference_track', 'unknown')
    import paper_metadata
    out = paper_metadata.normalize_publication(out)
    if out.get('journal') != doc.get('journal'):
        out['journal_abbr'] = app.journal_abbr(out.get('journal', ''))
    for key in ('journal_tier', 'tier_needs_review', 'catalogue_revision'):
        out.pop(key, None)
    tier_info = journal_catalogue.classify(app.DATA, out)
    out['journal_tier'] = tier_info.pop('tier')
    out.update(tier_info)
    for key in ('tier_basis', 'tier_source_url', 'tier_journal'):
        out.pop(key, None)
    out['cover_url'] = app.media_url(doc['id'], doc.get('cover') or 'page-001.jpg')
    out['translation'] = app.translation_counts(doc)
    out['page_count'] = len(doc.get('pages', []))
    out['original_url'] = app.media_url(doc['id'], 'original.pdf')
    structure = doc.get('reading_structure', {})
    out['structure_status'] = structure.get('status', 'local')
    out['structure_error'] = structure.get('error', '')
    try:
        with app.LOCK, app.db() as con:
            out['paper_ai'] = paper_ai.summary(con, doc['id'])
    except sqlite3.OperationalError as exc:
        if 'no such table: paper_ai' not in str(exc):
            raise
        # Pure metadata previews may run before init_db (including isolated tests).
        out['paper_ai'] = {'generation': 1, 'model': None,
                           'reasoning_effort': None, 'has_chat': False}
    if full:
        import copy
        from . import text_actions
        import paper_structure
        if structure.get('version') != paper_structure.VERSION or structure.get('source_fingerprint') != paper_structure.fingerprint(doc):
            structure = paper_structure.organize(doc)
        out['reading_structure'] = structure
        out['blocks'] = [{**b, 'translation_needs_review': text_actions.review_needed(b), **({'image_url': app.media_url(doc['id'], b['image'])} if b.get('image') else {})} for b in doc.get('blocks', [])]
        out['text_annotations'] = text_actions.resolved_annotations(doc)
        out['text_revision_history'] = copy.deepcopy(doc.get('text_revision_history', []))
        out['pages'] = [{**p, 'image_url': app.media_url(doc['id'], p.get('image', f"page-{p['number']:03}.jpg"))} for p in doc.get('pages', [])]
        out['figures'] = [{**f, 'url': app.media_url(doc['id'], f.get('path', ''))} for f in doc.get('figures', [])]
    return out


def patch_paper_fields(doc, fields):
    import paper_metadata
    publication_changed = any(key in fields and fields[key] != doc.get(key)
                              for key in (*paper_metadata.PUBLICATION_FIELDS, 'title', 'paper_type', 'conference_abbr'))
    doc.update(fields)
    saved = doc.get('metadata_enrichment')
    if publication_changed and isinstance(saved, dict) and saved.get('checked_at'):
        from .publication import assessment
        checked = saved['checked_at']
        saved = assessment(doc)
        saved.update(checked_at=None, previous_checked_at=checked,
                     status='needs_consent' if saved['missing'] else 'complete')
        doc['metadata_enrichment'] = saved
    provenance = doc.setdefault('metadata_provenance', {})
    for key in (*paper_metadata.PUBLICATION_FIELDS, 'title', 'paper_type', 'conference_abbr'):
        if key in fields:
            provenance[key] = {'source': 'manual'}
    if 'collection' in fields:
        doc['collection_assignment'] = {'status': 'manual', 'name': fields['collection'], 'error': ''}
