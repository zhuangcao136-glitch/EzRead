"""Source-preserving PDF semantic structure. No filesystem writes or model calls on import."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

VERSION = 1
ROLES = ('title', 'authors', 'affiliations', 'article_info', 'abstract',
         'section_heading', 'paragraph', 'caption', 'table', 'equation', 'reference', 'furniture')
FRONT = ('title', 'authors', 'affiliations', 'article_info', 'abstract')
LABELS = dict(title='Title', authors='Authors', affiliations='Affiliations',
              article_info='Article Info', abstract='Abstract')
_AFF = re.compile(r'\b(?:University|Universit[äé]|Institute|Department|Faculty|Laborator(?:y|ies)|School of|Academy|College|Research Centre|Research Center|Lab\b)', re.I)
_INFO = re.compile(r'^(?:Received|Revised|Accepted|Published|Available online|Keywords?|Key words|DOI\b|https?://(?:dx\.)?doi\.|Correspond(?:ing|ence)|E[-‐-]?mail|©|Copyright|The ORCID|Open access|Volume\b)|Creative Commons|\bISSN\b', re.I)
_SECTIONS = re.compile(r'^(?:\d+(?:\.\d+)*\.?\s+|[IVX]+\.\s+)?(?:Introduction|Background|Related work|Materials(?: and methods)?|Methods?|Methodology|Experimental(?: section| results| setup)?|Experiments?|Results?(?: and discussion)?|Discussion|Conclusions?|Summary|References|Bibliography|Acknowledg(?:e)?ments|Author contributions|Competing interests|Conflicts? of interest|Data availability|Code availability|Supporting information|Supplementary information|Appendix|Funding|Declaration|CRediT)\b', re.I)


def compact(text):
    return re.sub(r'[^a-z0-9]', '', str(text).casefold())


def fingerprint(doc):
    value = [(b['id'], b.get('text', ''), b.get('page'), b.get('bbox'), b.get('kind'),
              b.get('is_formula'), b.get('is_table')) for b in doc.get('blocks', [])]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def box(b):
    return b.get('bbox') or [0, 0, 1, 1]


def can_omit(b, doc=None):
    text, q = b.get('text', '').strip(), box(b)
    if b.get('note') or b.get('highlight') or b.get('translation_manually_edited'):
        return False
    repeated = False
    if doc and len(text) < 160 and (q[3] < .08 or q[1] > .935) and not heading(b):
        key = re.sub(r'\d+', '#', text.casefold())
        repeated = sum(re.sub(r'\d+', '#', other.get('text', '').strip().casefold()) == key and
                       (box(other)[3] < .08 or box(other)[1] > .935) for other in doc.get('blocks', [])) > 1
    return bool((re.fullmatch(r'\d+|\d+\s*\(\d+\s+of\s+\d+\)', text) and (q[1] > .9 or q[3] < .06))
                or (re.fullmatch(r'(?:www\.|https?://)(?!.*doi\.).+', text, re.I) and q[1] < .12)
                or re.fullmatch(r'RESEARCH ARTICLE|ORIGINAL ARTICLE|Review Article|Check for updates', text, re.I)
                or repeated)


def heading(b):
    text = b.get('text', '').strip()
    if len(text) > 180 or len(text.split()) > 22 or b.get('is_formula'):
        return False
    if _SECTIONS.fullmatch(text) and (text[:1].isupper() or text[:1].isdigit()):
        return True
    return bool(re.match(r'^(?:[IVX]+\.|[A-Z]\.)\s+[A-Z]', text) and len(text.split()) < 18 or
                b.get('kind') == 'heading' and re.match(r'^\d+(?:\.\d+)*\.?\s+[A-Z]', text) and
                not re.search(r'[.!?;]$', text))


def infer_roles(doc):
    blocks = doc.get('blocks', [])
    roles = {}
    first = [b for b in blocks if b.get('page') == 1]
    title = compact(doc.get('title', ''))
    authors = [compact(name) for name in doc.get('authors', [])]
    for b in blocks:
        text, key, q = b.get('text', '').strip(), compact(b.get('text', '')), box(b)
        role = ('equation' if b.get('is_formula') else 'table' if b.get('is_table') else
                'caption' if b.get('kind') == 'caption' else 'reference' if b.get('kind') == 'reference' else 'paragraph')
        if role == 'paragraph' and heading(b):
            role = 'section_heading'
        if can_omit(b, doc):
            role = 'furniture'
        if b.get('page') == 1 and role not in ('equation', 'table', 'caption', 'furniture'):
            if len(key) > 6 and title and (key in title or title in key) and q[1] < .45 and b.get('kind') == 'heading':
                role = 'title'
            elif key in ('abstract', 'summary') or re.match(r'^Abstract\s*[—–:-]', text, re.I):
                role = 'abstract'
            elif key in ('articleinfo', 'articleinformation', 'articlehistory', 'article', 'info') or _INFO.search(text) or re.match(r'^Contents lists|^journal homepage|^Dataset link', text, re.I) or (doc.get('journal') and compact(doc['journal']) in key and q[1] < .2 and len(text) < 160):
                role = 'article_info'
            elif _AFF.search(text) and len(text) < 2000 and (q[1] > .55 or q[3] < .4):
                role = 'affiliations'
            elif any(len(a) > 8 and (a in key or len(key) > 8 and re.sub(r'^and', '', key) in a) for a in authors) and q[1] < .4:
                role = 'authors'
            elif b.get('kind') == 'heading' and len(text) > 450 and q[1] < .4:
                role = 'abstract'
            elif len(text) > 450 and q[1] < .35 and q[3] < .7 and q[2] - q[0] > .5:
                role = 'abstract'
        roles[b['id']] = role
    # Recover an explicitly labelled abstract by its physical region, even when
    # the other column contains metadata or Introduction at the same height.
    labels = [b for b in first if compact(b.get('text', '')) == 'abstract']
    for label in labels:
        lq = box(label)
        barriers = [box(b)[1] for b in first if box(b)[1] > lq[3] and
                    (roles[b['id']] == 'section_heading' or re.match(r'^Keywords?\b', b.get('text', ''), re.I))]
        stop = min(barriers, default=.78)
        for b in first:
            q = box(b)
            if (lq[1] <= q[1] < stop and roles[b['id']] in ('paragraph', 'section_heading') and
                    abs(q[0] - lq[0]) < .08 and
                    not _SECTIONS.match(b.get('text', ''))):
                roles[b['id']] = 'abstract'
    # Article Info often occupies the small sidebar beside the abstract.
    info = [b for b in first if compact(b.get('text', '')) in ('articleinfo', 'article', 'info')]
    for label in info:
        lq = box(label)
        abstract = [b for b in first if roles[b['id']] == 'abstract' and box(b)[0] > lq[0] + .1]
        stop = max((box(b)[3] for b in abstract), default=lq[3] + .1)
        for b in first:
            q = box(b)
            if lq[1] <= q[1] < stop and q[2] < .34 and roles[b['id']] in ('paragraph', 'section_heading'):
                roles[b['id']] = 'article_info'
    # Tiny affiliation superscripts follow the role of the nearest byline/footnote.
    for b in first:
        if not re.fullmatch(r'[\d,;*†‡✉\s]+|[a-z]', b.get('text', '').strip()):
            continue
        q = box(b)
        nearby = [other for other in first if roles[other['id']] in ('authors', 'affiliations') and
                  abs(box(other)[1] - q[1]) < .025 and
                  box(other)[0] - .04 <= q[0] <= box(other)[2] + .04]
        if nearby:
            roles[b['id']] = roles[min(nearby, key=lambda other: abs(box(other)[1] - q[1]))['id']]
    return roles


def spatial_order(blocks):
    rows = []
    for b in sorted(blocks, key=lambda b: (b.get('page', 1), box(b)[1], box(b)[0])):
        if rows and b.get('page') == rows[-1][0].get('page') and abs(box(b)[1] - box(rows[-1][0])[1]) < .009:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [b for row in rows for b in sorted(row, key=lambda b: box(b)[0])]


def joinable(a, b, role):
    if role in ('title', 'authors', 'abstract'):
        # Structured abstracts keep internal headings/labels separate.
        return role != 'abstract' or not (compact(a.get('text', '')) == 'abstract' or
               compact(b.get('text', '')) == 'abstract' or heading(b) and len(b.get('text', '')) < 100)
    if role == 'affiliations' and re.fullmatch(r'[\d,a-z*†‡\s]+', a.get('text', '').strip()):
        return a.get('page') == b.get('page') and abs(box(a)[1] - box(b)[1]) < .03
    if role != 'paragraph' or a.get('image') or b.get('image'):
        return False
    aq, bq = box(a), box(b)
    tail = a.get('text', '').rstrip()
    lead = b.get('text', '').lstrip()
    same_page = a.get('page') == b.get('page')
    same_column = abs(aq[0] - bq[0]) < .075
    small = len(lead) < 35 or len(tail) < 35
    same_row = same_page and abs(aq[1] - bq[1]) < .014
    boundary = ((same_page and not same_column and aq[3] > .64 and bq[1] < .4)
                or (b.get('page', 1) == a.get('page', 1) + 1 and aq[3] > .65 and bq[1] < .4))
    continuous = same_page and same_column and -.02 < bq[1] - aq[3] < .055
    unfinished = bool(tail and not re.search(r'[.!?:;][”"\)\]\d\s]*$', tail))
    return bool((small and same_row) or (unfinished and (continuous or boundary) and
                (lead[:1].islower() or len(lead) < 35 or tail.endswith('-'))))


def audit_structure(doc, structure):
    expected = [b['id'] for b in doc.get('blocks', [])]
    actual = [bid for section in structure.get('sections', []) for entry in section['entries'] for bid in entry['source_ids']]
    actual += [item['id'] for item in structure.get('omitted', [])]
    if len(expected) != len(set(expected)) or len(actual) != len(set(actual)) or set(expected) != set(actual):
        raise ValueError('论文结构校验失败：存在丢失、重复或未知源文本块。')
    return {'source_blocks': len(expected), 'represented_blocks': len(actual),
            'paragraphs': sum(len(s['entries']) for s in structure['sections']), 'complete': True}


def organize(doc, roles=None):
    blocks = doc.get('blocks', [])
    by_id = {b['id']: b for b in blocks}
    roles = roles or infer_roles(doc)
    # Keep all state local: imports and reader requests may run concurrently.
    def grouped(items, role):
        result = []
        for b in items:
            if result and joinable(by_id[result[-1]['source_ids'][-1]], b, role):
                result[-1]['source_ids'].append(b['id'])
            else:
                result.append({'id': b['id'], 'role': role, 'source_ids': [b['id']]})
        return result
    sections, omitted = [], []
    for role in FRONT:
        selected = [b for b in blocks if roles.get(b['id']) == role]
        if selected:
            sections.append({'id': role, 'role': role, 'label': LABELS[role],
                             'entries': grouped(spatial_order(selected), role)})
    current = None
    for b in blocks:
        role = roles.get(b['id'], 'paragraph')
        if role in FRONT:
            continue
        if role == 'furniture' and can_omit(b, doc):
            omitted.append({'id': b['id'], 'reason': '重复页眉页脚、独立页码或出版商版面标记'})
            continue
        if role == 'section_heading':
            current = {'id': 'section-' + b['id'], 'role': 'body', 'label': b.get('text', ''),
                       'entries': [{'id': b['id'], 'role': role, 'source_ids': [b['id']]}]}
            sections.append(current)
            continue
        if current is None:
            current = {'id': 'body', 'role': 'body', 'label': 'Main text', 'entries': []}
            sections.append(current)
        entries = current['entries']
        if entries and entries[-1]['role'] == role and joinable(by_id[entries[-1]['source_ids'][-1]], b, role):
            entries[-1]['source_ids'].append(b['id'])
        else:
            entries.append({'id': b['id'], 'role': role, 'source_ids': [b['id']]})
    result = {'version': VERSION, 'skill': 'ezread-paper-import', 'source_fingerprint': fingerprint(doc),
              'method': 'local', 'status': 'local', 'sections': sections, 'omitted': omitted,
              'warnings': [], 'error': ''}
    if not blocks:
        result['warnings'].append('未检测到可选文字；扫描件需要 OCR，请查看原 PDF。')
    if not any(s['role'] == 'abstract' for s in sections) and blocks:
        result['warnings'].append('未可靠识别摘要，保留原文供核对。')
    if not any(s['role'] == 'affiliations' for s in sections) and blocks:
        result['warnings'].append('未可靠识别作者单位，不推断实验室归属。')
    result['audit'] = audit_structure(doc, result)
    return result


def candidates(doc):
    blocks = doc.get('blocks', [])
    local = infer_roles(doc)
    result, size = [], 0
    for i, b in enumerate(blocks):
        if not (b.get('page') == 1 or heading(b) or b.get('kind') == 'heading' and len(b.get('text', '')) < 180):
            continue
        text = b.get('text', '')
        if size + len(text) > 36000:
            continue
        size += len(text)
        result.append({'id': b['id'], 'text': text, 'page': b.get('page'), 'bbox': b.get('bbox'),
                       'local_role': local[b['id']], 'previous': blocks[i-1].get('text', '')[-160:] if i else '',
                       'next': blocks[i+1].get('text', '')[:160] if i+1 < len(blocks) else ''})
    return result


def validate_roles(doc, selected, result):
    values = result.get('roles')
    if not isinstance(values, list):
        raise ValueError('语义校对未返回文本分类。')
    expected = {b['id'] for b in selected}
    roles, by_id = {}, {b['id']: b for b in doc.get('blocks', [])}
    for item in values:
        bid, role = item.get('id'), item.get('role')
        if bid not in expected or bid in roles or role not in ROLES:
            raise ValueError('语义校对返回重复、未知 ID 或不支持的分类。')
        if role == 'furniture' and not can_omit(by_id[bid], doc):
            raise ValueError('语义校对试图移除有意义源文，已保留本地结构。')
        if by_id[bid].get('is_formula') and role != 'equation':
            raise ValueError('语义校对改变公式类型，已保留本地结构。')
        roles[bid] = role
    if set(roles) != expected:
        raise ValueError('语义校对遗漏候选文本，已保留本地结构。')
    return roles


def refine(doc, config, *, runner=None, cancel_event=None):
    if runner is None:
        import codex_bridge
        runner = codex_bridge._run_json
    selected = candidates(doc)
    if not selected:
        return organize(doc)
    root = Path(__file__).resolve().parents[1]
    instruction = ((root / 'SKILL.md').read_text(encoding='utf-8') + '\n\n' +
                   (root / 'references' / 'semantic.md').read_text(encoding='utf-8'))
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['roles'], 'properties': {
        'roles': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['id', 'role'], 'properties': {'id': {'type': 'string'},
        'role': {'type': 'string', 'enum': list(ROLES)}}}}}}
    response = runner(instruction, {'candidates': selected}, schema, timeout=240,
                      cancel_event=cancel_event, **config)
    roles = infer_roles(doc)
    roles.update(validate_roles(doc, selected, response))
    result = organize(doc, roles)
    result.update(method='local+ai', status='ready', model_config=dict(config))
    return result


def selection_source(doc, block_id, source_ids=None):
    ids = source_ids or [block_id]
    if not isinstance(ids, list) or not ids or len(ids) > 50 or block_id not in ids or len(set(ids)) != len(ids):
        raise ValueError('划选原文段落无效。')
    structure = doc.get('reading_structure') or organize(doc)
    if len(ids) > 1 and not any(entry['source_ids'] == ids for s in structure['sections'] for entry in s['entries']):
        raise ValueError('只能在同一自然段内连续划选。')
    by_id = {b['id']: b for b in doc.get('blocks', [])}
    selected = [by_id.get(bid) for bid in ids]
    if any(not b or b.get('translation') or b.get('is_formula') or b.get('kind') == 'reference' for b in selected):
        raise ValueError('只能在右侧尚未翻译的英文正文中划线。')
    return ' '.join(b.get('text', '') for b in selected)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Organize an extracted EzRead document JSON without rewriting source blocks.')
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    document = json.loads(args.input.read_text(encoding='utf-8'))
    document = document.get('paper', document)
    structure = organize(document)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(structure['audit'], ensure_ascii=False))
