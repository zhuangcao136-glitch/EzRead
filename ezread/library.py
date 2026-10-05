"""Paper metadata, public documents and native file actions."""
from __future__ import annotations
from .context import ApplicationContext
import json
import re
import shutil
import sqlite3
import urllib.parse
import urllib.request


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


def crossref_metadata(doi):
    if not doi:
        return {}
    url = 'https://api.crossref.org/works/' + urllib.parse.quote(doi, safe='')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'EzRead/2.0 (personal desktop reference library)'})
        with urllib.request.urlopen(req, timeout=7) as response:
            item = json.load(response)['message']
        authors = [' '.join(filter(None, [a.get('given'), a.get('family')])) for a in item.get('author', [])]
        affiliations = sorted({x['name'] for a in item.get('author', []) for x in a.get('affiliation', []) if x.get('name')})
        dates = item.get('published', item.get('issued', {})).get('date-parts', [[]])[0]
        venue = (item.get('container-title') or [''])[0]
        record_type = item.get('type')
        event = item.get('event') if isinstance(item.get('event'), dict) else {}
        classification = {}
        if record_type == 'journal-article':
            classification = {'paper_type': 'journal'}
        elif record_type in ('proceedings-article', 'proceedings'):
            conference = event.get('name') or venue
            classification = {'paper_type': 'conference', 'conference_name': conference,
                              'conference_track': 'workshop' if re.search(r'\bworkshop|companion\b', conference, re.I) else 'unknown'}
        elif record_type == 'posted-content':
            classification = {'paper_type': 'preprint'}
        # An arXiv identifier alone says nothing about later publication status.
        return {'title': (item.get('title') or [''])[0], 'journal': venue if record_type == 'journal-article' else '',
                'authors': authors, 'affiliations': affiliations, 'year': dates[0] if dates else None,
                'metadata_source': url, 'crossref_type': record_type, **classification}
    except Exception:
        return {}


def media_url(pid, filename):
    return '/media/' + pid + '/' + urllib.parse.quote(str(filename).replace('\\', '/'), safe='/')


def translation_counts(app: ApplicationContext, doc):
    target = [b for b in doc.get('blocks', []) if b.get('kind') != 'reference' and not b.get('is_formula') and b.get('text', '').strip()]
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
    import journal_tiers
    import paper_ai
    hidden = {'hash', 'blocks', 'pages', 'figures', 'translation_versions', 'translation_current',
              'translation_runs', 'translation_drafts', 'jif', 'jif_year', 'jif_source_url',
              'quartiles', 'metrics_source', 'metrics_sources', 'metrics_status', 'metrics_error',
              'metrics_note', 'conference_rankings', 'read_state', 'reading_structure'}
    out = {k: v for k, v in doc.items() if k not in hidden}
    out.setdefault('paper_type', 'journal')
    out.setdefault('conference_name', '')
    out.setdefault('conference_abbr', '')
    out.setdefault('conference_track', 'unknown')
    tier_info = journal_tiers.classify(out)
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
        import paper_structure
        if structure.get('version') != paper_structure.VERSION or structure.get('source_fingerprint') != paper_structure.fingerprint(doc):
            structure = paper_structure.organize(doc)
        out['reading_structure'] = structure
        out['blocks'] = [{**b, **({'image_url': app.media_url(doc['id'], b['image'])} if b.get('image') else {})} for b in doc.get('blocks', [])]
        out['pages'] = [{**p, 'image_url': app.media_url(doc['id'], p.get('image', f"page-{p['number']:03}.jpg"))} for p in doc.get('pages', [])]
        out['figures'] = [{**f, 'url': app.media_url(doc['id'], f.get('path', ''))} for f in doc.get('figures', [])]
    return out


def patch_paper_fields(doc, fields):
    doc.update(fields)
    if 'collection' in fields:
        doc['collection_assignment'] = {'status': 'manual', 'name': fields['collection'], 'error': ''}
