"""Publication lookup, explicitly requested by the user, with conservative matching."""
from __future__ import annotations
import copy
from difflib import SequenceMatcher
import html
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from .config import VERSION
from .context import ApplicationContext
import paper_metadata


def assessment(doc):
    saved = dict(doc.get('metadata_enrichment') or {})
    saved['missing'] = paper_metadata.missing_fields(doc)
    saved.setdefault('status', 'needs_consent' if saved['missing'] else 'complete')
    if saved['missing'] and saved['status'] == 'complete':
        saved['status'] = 'needs_consent'
    if (saved['status'] == 'declined' and saved.get('checked_at') and not saved['missing']
            and not saved.get('conflicts') and not saved.get('error')):
        # Older dialogs overwrote a successful check when an optional recheck was closed.
        saved['status'] = 'complete'
    return saved


def _read(url, json_result=True):
    request = urllib.request.Request(url, headers={'User-Agent': f'EzRead/{VERSION} (publication metadata lookup)',
                                                  'Accept': 'application/json' if json_result else 'text/html'})
    with urllib.request.urlopen(request, timeout=8) as response:
        content = response.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError('出版信息响应过大，请稍后重试。')
    return json.loads(content) if json_result else content.decode('utf-8', errors='replace')


def _normalized(value):
    value = html.unescape(re.sub(r'<[^>]*>', '', str(value)))
    return ''.join(c for c in unicodedata.normalize('NFKD', value).casefold() if c.isalnum())


def _identifying_title(value):
    title = _normalized(value)
    return len(title) >= (6 if re.search(r'[\u3400-\u9fff]', title) else 15)


def _title_score(doc, item):
    title = _normalized(doc.get('title', ''))
    remote = _normalized((item.get('title') or [''])[0])
    return SequenceMatcher(None, title, remote).ratio() if _identifying_title(title) and remote else 0


def _matches(doc, item):
    score = _title_score(doc, item)
    if score < .94:
        return 0
    # Compare full names as well as surnames. Chinese bylines commonly have no
    # spaces, while bibliographic records split given/family in either order.
    local_names = {name for s in paper_metadata.valid_authors(doc.get('authors'))
                   for name in (_normalized(s), _normalized(s.split()[-1]))}
    remote_names = {name for a in item.get('author') or [] for name in (
        _normalized(a.get('family', '')), _normalized(str(a.get('given') or '') + str(a.get('family') or '')),
        _normalized(str(a.get('family') or '') + str(a.get('given') or '')))} - {''}
    if local_names and remote_names and not local_names.intersection(remote_names):
        return 0
    # Titles which are not identical need author corroboration.
    if score < .985 and not local_names.intersection(remote_names):
        return 0
    return score


def _date(item, *keys):
    from datetime import date
    for key in keys:
        parts = item.get(key, {}).get('date-parts', [[]])[0]
        try:
            if len(parts) == 3:
                return date(*parts).isoformat()
            if len(parts) in (1, 2) and 1800 <= int(parts[0]) <= 2200:
                if len(parts) == 2 and not 1 <= int(parts[1]) <= 12:
                    continue
                return '-'.join(str(int(p)) if i == 0 else f'{int(p):02}' for i, p in enumerate(parts))
        except (ValueError, TypeError):
            pass
    return ''


def record(item):
    doi = str(item.get('DOI') or '').strip()
    source = 'https://api.crossref.org/works/' + urllib.parse.quote(doi, safe='')
    kind = item.get('type')
    venue = (item.get('container-title') or [''])[0]
    publication_date = _date(item, 'published', 'published-print', 'published-online', 'issued')
    return paper_metadata.normalize_publication({'doi': doi, 'journal': venue if kind == 'journal-article' else '',
        'conference_name': ((item.get('event') or {}).get('name') or venue) if kind == 'proceedings-article' else '',
        'paper_type': {'journal-article': 'journal', 'proceedings-article': 'conference', 'posted-content': 'preprint'}.get(kind, 'other'),
        'authors': [' '.join(filter(None, [a.get('given'), a.get('family')])) for a in item.get('author') or []],
        'year': int(publication_date[:4]) if publication_date else None, 'publication_date': publication_date,
        'online_date': _date(item, 'published-online'), 'print_date': _date(item, 'published-print'),
        'volume': str(item.get('volume') or ''), 'issue': str(item.get('issue') or ''),
        'page_range': str(item.get('page') or ''), 'article_number': str(item.get('article-number') or ''),
        'metadata_source': source, 'crossref_type': kind})


def lookup(doc):
    doi = str(doc.get('doi') or '').strip()
    errors = []
    if doi and not doi.casefold().startswith('10.48550/arxiv.'):
        try:
            item = _read('https://api.crossref.org/works/' + urllib.parse.quote(doi, safe=''))['message']
            if _matches(doc, item):
                return record(item)
        except (OSError, ValueError, KeyError) as exc:
            errors.append(exc)
    query = ' '.join([str(doc.get('title') or ''), *paper_metadata.valid_authors(doc.get('authors'))[:2]])[:800]
    title_conflict = False
    if not _identifying_title(doc.get('title', '')):
        raise ValueError('论文标题信息不足，请先编辑原文标题，再联网补全。')
    try:
        url = 'https://api.crossref.org/works?' + urllib.parse.urlencode({'query.bibliographic': query, 'rows': 5})
        items = _read(url)['message']['items']
        candidates = sorted([(_matches(doc, item), item) for item in items
                             if item.get('type') in ('journal-article', 'proceedings-article')], key=lambda entry: entry[0], reverse=True)
        matches = [(score, item) for score, item in candidates if score]
        title_conflict = any(_title_score(doc, item) >= .94 for _, item in candidates)
        if matches:
            if len(matches) > 1 and matches[0][0] - matches[1][0] < .025 and matches[0][1].get('DOI') != matches[1][1].get('DOI'):
                raise ValueError('找到了多个相近的正式出版记录，无法可靠确定；请手动核对 DOI。')
            return record(matches[0][1])
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        errors.append(exc)
    arxiv = str(doc.get('arxiv_id') or '')
    if re.fullmatch(r'\d{4}\.\d{4,5}', arxiv):
        try:
            page = _read('https://arxiv.org/abs/' + arxiv, False)
            linked = re.search(r'<meta\s+name=["\']citation_doi["\']\s+content=["\'](10\.(?!48550)[^"\']+)', page, re.I)
            if linked:
                item = _read('https://api.crossref.org/works/' + urllib.parse.quote(html.unescape(linked.group(1)), safe=''))['message']
                if _matches(doc, item):
                    return record(item)
        except (OSError, ValueError, KeyError) as exc:
            errors.append(exc)
    if errors:
        raise ValueError('联网查询未完成，可能是网络不可用或出版信息服务暂时无响应。已保留现有信息，可稍后重试。')
    if title_conflict:
        raise ValueError('找到了标题相近的正式出版记录，但作者信息不一致。已保留现有信息，请核对作者或 DOI。')
    raise ValueError('未找到与标题和作者可靠匹配的正式出版记录。已保留现有信息，请手动核对或稍后重试。')


def enrich(app: ApplicationContext, pid, data):
    if not isinstance(data, dict) or set(data) != {'consent'} or not isinstance(data['consent'], bool):
        raise ValueError('请明确选择是否允许联网补全出版信息。')
    original = app.get_doc(pid)
    if not data['consent']:
        def decline(current):
            saved = assessment(current)
            if saved['missing']:
                saved.update(status='declined', error='')
            current['metadata_enrichment'] = saved
        app.update_doc(pid, decline)
        return app.get_doc(pid)
    # Backfill local fields of old imports in memory; do not re-import or change source blocks.
    normalized_original = paper_metadata.normalize_publication(original)
    search_doc = copy.deepcopy(normalized_original)
    search_doc['authors'] = paper_metadata.valid_authors(search_doc.get('authors'))
    path = app.LIBRARY / pid / 'original.pdf'
    if path.is_file():
        import pdfplumber, pdf_tools
        with pdfplumber.open(path) as pdf:
            first_lines = pdf_tools._lines(pdf.pages[0])
            local = paper_metadata.extract(pdf.metadata or {}, [b for b in original.get('blocks', []) if b.get('page') == 1], first_lines, pdf.pages[0].height,
                                           first_text=pdf.pages[0].extract_text() or '', title=original.get('title', ''))
            for key, value in local.items():
                if value and not search_doc.get(key):
                    search_doc[key] = value
    try:
        found = lookup(search_doc)
    except ValueError as exc:
        error = str(exc)
        app.update_doc(pid, lambda d: d.update(metadata_enrichment={**assessment(d), 'status': 'error', 'error': error}))
        return app.get_doc(pid)
    def commit(current):
        provenance = current.setdefault('metadata_provenance', {})
        conflicts = []
        filled = []
        corrected = []
        # A validated conference record can repair a PDF conference header that
        # older importers put into journal. Determine this from the snapshot,
        # before any field is changed, and keep manual/concurrent edits intact.
        conference_repair = (found.get('paper_type') == 'conference'
            and normalized_original.get('paper_type') == 'conference'
            and normalized_original.get('journal') != original.get('journal')
            and current.get('paper_type') == original.get('paper_type')
            and provenance.get('paper_type', {}).get('source') != 'manual')
        for key in (*paper_metadata.PUBLICATION_FIELDS, 'online_date', 'print_date', 'paper_type', 'conference_abbr'):
            value = found.get(key)
            clear_legacy_journal = conference_repair and key == 'journal'
            if not value and not clear_legacy_journal:
                continue
            old = current.get(key)
            # Keep manual edits and concurrent changes, including intentional clearing.
            if old != original.get(key) or provenance.get(key, {}).get('source') == 'manual':
                continue
            if key in ('publication_date', 'online_date', 'print_date') and current.get('year') and found.get('year') != current['year']:
                conflicts.append(key)
                continue
            if key == 'year' and current.get('publication_date') and str(value) != str(current['publication_date'])[:4]:
                conflicts.append(key)
                continue
            replace_arxiv_doi = key == 'doi' and str(old or '').lower().startswith('10.48550/arxiv.')
            # Correct demonstrably malformed automatic fields; never overwrite valid conflicts.
            automatic = provenance.get(key, {}).get('source') in (None, 'pdf')
            repair = automatic and (clear_legacy_journal
                or (key == 'paper_type' and old in (None, 'other', 'preprint'))
                or (key == 'paper_type' and conference_repair)
                or (key == 'authors' and paper_metadata.valid_authors(old) != old)
                or (key == 'journal' and paper_metadata.clean_venue(old) != old)
                or (key == 'publication_date' and len(str(old or '')) in (4, 7)
                    and str(value).startswith(str(old)) and len(str(value)) > len(str(old))))
            if not old or replace_arxiv_doi or repair:
                current[key] = value
                provenance[key] = {'source': 'crossref', 'url': found['metadata_source']}
                filled.append(key)
                if old and repair and _normalized(old) != _normalized(value) and not (key == 'paper_type' and old in ('other', 'preprint')):
                    corrected.append(key)
            elif _normalized(old) != _normalized(value):
                conflicts.append(key)
        if 'journal' in filled:
            current['journal_abbr'] = app.journal_abbr(current['journal'])
        current['metadata_source'] = found['metadata_source']
        current['crossref_type'] = found['crossref_type']
        remaining = paper_metadata.missing_fields(current)
        current['metadata_enrichment'] = {'status': 'partial' if remaining or conflicts else 'complete',
            'missing': remaining, 'filled': filled, 'conflicts': conflicts, 'checked_at': app.now(),
            'corrected': corrected,
            'error': '检索记录与已有字段有差异，已保留已有信息，请核对。' if conflicts else ''}
    app.update_doc(pid, commit)
    return app.get_doc(pid)
