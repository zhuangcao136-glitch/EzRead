"""Conservative, offline publication extraction. Never infer dates from file creation."""
import calendar
from datetime import date
from difflib import SequenceMatcher
import re
import unicodedata

PUBLICATION_FIELDS = ('journal', 'conference_name', 'authors', 'doi', 'year', 'publication_date',
                      'volume', 'issue', 'page_range', 'article_number')
_DOI = re.compile(r'10\.\d{4,9}/[-._;()/:A-Z0-9]+', re.I)
_MONTHS = {name.lower(): i for i in range(1, 13) for name in (calendar.month_name[i], calendar.month_abbr[i])}
_MONTH = '|'.join(sorted(_MONTHS, key=len, reverse=True))
_DATE = re.compile(r'(?:\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}\s+(?:' + _MONTH + r')\.?\s+\d{4}\b|\b(?:' + _MONTH + r')\.?\s+\d{1,2},?\s+\d{4}\b|\b(?:19|20)\d{2}\b)', re.I)


def parse_date(text):
    found = _DATE.search(text)
    if not found:
        return ''
    parts = found.group().replace(',', '').replace('.', '').split()
    try:
        if len(parts) == 1:
            numeric = re.split(r'[-/]', parts[0])
            if len(numeric) == 1:
                return numeric[0]
            return date(*map(int, numeric)).isoformat()
        if parts[0].isdigit():
            return date(int(parts[2]), _MONTHS[parts[1].lower()], int(parts[0])).isoformat()
        return date(int(parts[2]), _MONTHS[parts[0].lower()], int(parts[1])).isoformat()
    except (ValueError, KeyError):
        return ''


_NON_AUTHOR = re.compile(r'©|\b(?:copyright|license[ed]|licensee|reserved|rights|government|'
    r'University|Department|Institutes?|Abstract|Received|Accepted|Revised|Published|Keywords|Engineering|Laboratory|Journal|'
    r'Centres?|Centers?|Resources?|Devices|'
    r'Acrobat|Adobe|PDFMaker|Microsoft|corresponding|affiliation|association\s+for|advancement\s+of|'
    r'(?:school|faculty|college|academy)\s+of|original\s+U\.?S|no\s+claim|anonymous\s+submission|early\s+access)\b|'
    r'大学|学院|研究所|实验室|版权|通讯作者|作者信息|摘要|关键词', re.I)


def _names(text):
    if '@' in text or _NON_AUTHOR.search(text):
        return []
    text = re.sub(r'\b\d+(?:st|nd|rd|th)\b', '', text, flags=re.I)
    text = re.sub(r'\d+|[✉*∗†‡§\ue000-\uf8ff]', '', text)
    text = re.sub(r'\((?:first|corresponding)\s+author\)', '', text, flags=re.I)
    text = re.sub(r'(?<=\w)-\s+(?=\w)', '-', text)
    # Letter affiliation labels are often attached to names by PDF extraction.
    text = re.sub(r'(?<=\S)\s+[a-z](?:\s*,\s*[a-z])*(?=\s*(?:,|$))', '', text)
    names = [re.sub(r'^and\s+', '', s.strip(' ,;& '), flags=re.I) for s in
             re.split(r'\s*[,;，、；·]\s*|\s*&\s*|\s+and\s+', text) if s.strip(' ,;& ')]
    if names and all(re.fullmatch(r'[\u3400-\u9fff]{2,8}', part) for name in names for part in name.split()):
        return [part for name in names for part in name.split()]
    if any(re.search(r'[\u3400-\u9fff]|\ufffd|[:=/]', name) for name in names):
        return []
    return names if all(1 < len(s.split()) <= 7 and len(s) < 80
                        and sum(word[0].isupper() for word in s.split()) >= 2 for s in names) else []


def valid_authors(value):
    """Discard identifiable non-person text, including already imported copyright lines."""
    values = value if isinstance(value, (list, tuple)) else [value]
    return list(dict.fromkeys(name for entry in values if isinstance(entry, str)
                             for name in _names(entry)))


def clean_venue(value):
    """PDF Subject sometimes contains a whole citation rather than a venue name."""
    name = re.split(r',?\s*(?:doi[:\s]|https?://)|\s*\|', str(value or ''), maxsplit=1, flags=re.I)[0].strip()
    name = re.split(r'\s+(?:19|20)\d{2}(?:[.;:\s]|$)', name, maxsplit=1)[0].strip()
    doi = _DOI.search(name)
    if doi:
        name = name[:doi.start()].strip(' ,;:')
    if not any(c.isalpha() for c in name) or re.search(r'^\s*(?:submitted\s+to|manuscript|abstract|keywords?)\b', name, re.I):
        return ''
    return name


def is_conference_venue(value):
    text = str(value or '')
    return bool(re.search(r'\b(?:conference|symposium|workshop)\b', text, re.I)
                and not re.search(r'\b(?:journal|transactions|letters)\b', text, re.I))


def conference_abbreviation(name, saved=''):
    """Use an explicit acronym or established venue name, never invented initials."""
    if str(saved or '').strip():
        return str(saved).strip()
    canonical = {'case': 'CASE', 'robosoft': 'RoboSoft', 'icra': 'ICRA', 'iros': 'IROS'}
    excluded = {'IEEE', 'ACM', 'IET', 'RAS', 'RSJ', 'USA', 'CONFERENCE', 'SYMPOSIUM', 'WORKSHOP', 'PROCEEDINGS'}
    text = str(name or '').strip()
    for match in re.finditer(r'\(([A-Za-z][A-Za-z-]{1,19})(?:\s*[-\u2019\']?\s*\d{2,4})?\)', text):
        acronym = match.group(1).rstrip('-')
        if acronym.upper() not in excluded and (acronym.casefold() in canonical or sum(c.isupper() for c in acronym) >= 2):
            return canonical.get(acronym.casefold(), acronym)
    compact = _normalized(text)
    for phrase, acronym in [('internationalconferenceonautomationscienceandengineering', 'CASE'),
                            ('internationalconferenceonsoftrobotics', 'RoboSoft'),
                            ('internationalconferenceonroboticsandautomation', 'ICRA'),
                            ('internationalconferenceonintelligentrobotsandsystems', 'IROS')]:
        if phrase in compact:
            return acronym
    short = re.sub(r'\b(?:19|20)\d{2}\b', '', text).strip()
    if short.upper() not in excluded and re.fullmatch(r'[A-Za-z][A-Za-z-]{1,19}', short) and sum(c.isupper() for c in short) >= 2:
        return canonical.get(short.casefold(), short)
    return ''


def _normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text)).casefold() if c.isalnum())


def normalize_publication(doc):
    """One non-mutating normalization used by import, lookup and public display."""
    out = dict(doc)
    provenance = doc.get('metadata_provenance') or {}
    if provenance.get('authors', {}).get('source') == 'pdf':
        out['authors'] = valid_authors(doc.get('authors'))
    venue = doc.get('journal') or ''
    if provenance.get('journal', {}).get('source') == 'pdf':
        out['journal'] = clean_venue(venue)
    kind = doc.get('paper_type')
    if (kind in (None, 'journal', 'other', 'conference') and is_conference_venue(venue)
            and provenance.get('journal', {}).get('source') == 'pdf'
            and provenance.get('paper_type', {}).get('source') != 'manual'
            and doc.get('crossref_type') != 'journal-article'):
        out['paper_type'] = kind = 'conference'
        if provenance.get('conference_name', {}).get('source') != 'manual':
            out['conference_name'] = doc.get('conference_name') or venue
        out['journal'] = ''
        out['journal_abbr'] = ''
    if kind == 'conference':
        if provenance.get('conference_abbr', {}).get('source') == 'manual':
            out['conference_abbr'] = str(out.get('conference_abbr') or '').strip()
        else:
            out['conference_abbr'] = conference_abbreviation(out.get('conference_name'), out.get('conference_abbr'))
    return out


_NON_TITLE = re.compile(r'^(?:https?://|www\.|doi\b|issn\b|isbn\b|abstract\b|introduction\b|'
    r'references\b|keywords?\b|received\b|accepted\b|published\b|submitted\s+to\b|manuscript\b|'
    r'please\s+cite\b|contents\s+lists\b|journal\s+homepage\b|journal\s+of\b|'
    r'IEEE.*(?:transactions|journal)\b|(?:research|review|original|regular)\s+(?:article|paper|issue)\b|article$)|'
    r'copyright|©|第\s*\d+\s*[卷期]|收稿|录用|摘\s*要|关键词', re.I)


def title_evidence(raw, blocks, lines, page_height):
    """Find a visible, complete title without relying on the first heading role."""
    ordered = sorted(lines, key=lambda line: (line['bbox'][1], line['bbox'][0]))
    homepage_bottom = max((line['bbox'][3] for line in ordered
        if re.search(r'journal\s+homepage|contents\s+lists\s+available', line['text'], re.I)
        and line['bbox'][1] < page_height * .4), default=0)
    lower = max(page_height * .025, homepage_bottom)
    candidates = []
    for line in ordered:
        value = line['text'].strip(' *†‡✉✩☆')
        length = len(_normalized(value))
        if (lower <= line['bbox'][1] < page_height * .48 and 6 <= length <= 180
                and not _NON_TITLE.search(value) and not re.fullmatch(r'[A-Z\s]+ARTICLE', value)):
            candidates.append({**line, 'text': value})
    target = str(raw.get('Title') or '').strip(' *†‡✉✩☆')
    usable = (15 <= len(target) <= 600 and not _NON_TITLE.search(target)
              and not re.search(r'untitled|microsoft\s+word|\.(?:docx?|pdf|tex)$', target, re.I))
    matching = [line for line in candidates if usable and _normalized(line['text']) in _normalized(target)]
    if matching:
        largest_match = max(line.get('size', 0) for line in matching)
        matching = [line for line in matching if line.get('size', 0) >= largest_match * .9]
        contiguous = []
        bottom = matching[0]['bbox'][1]
        for line in matching:
            if contiguous and line['bbox'][1] - bottom > max(6, largest_match * .85):
                break
            contiguous.append(line)
            bottom = max(bottom, line['bbox'][3])
        matching = contiguous
    if matching and sum(len(_normalized(line['text'])) for line in matching) >= len(_normalized(target)) * .65:
        chosen = matching
        title = target
    elif candidates:
        anchors = [line for line in candidates if len(_normalized(line['text'])) >= 15
                   or re.search(r'[\u3400-\u9fff]', line['text'])]
        largest = max((line.get('size', 0) for line in anchors), default=0)
        if largest:
            anchor = next(line for line in candidates if line.get('size', 0) == largest)
            chosen = []
            bottom = anchor['bbox'][1]
            for line in candidates:
                if line['bbox'][1] < anchor['bbox'][1] - 1:
                    continue
                if line.get('size', 0) < largest * .9:
                    continue
                # Typography alone cannot separate a same-font title and byline.
                # A title is a contiguous group, including spans on the same row.
                if (line['bbox'][1] - bottom > max(6, largest * .85)
                        or line['bbox'][1] > anchor['bbox'][1] + page_height * .16):
                    break
                chosen.append(line)
                bottom = max(bottom, line['bbox'][3])
        else:
            # Older fixtures/source blocks may lack font sizes. Ignore running labels.
            headings = [b for b in blocks if b.get('kind') == 'heading' and not _NON_TITLE.search(b.get('text', ''))]
            chosen = [line for line in candidates if any(_normalized(line['text']) in _normalized(b['text']) for b in headings)]
            if not chosen:
                chosen = []
        title = ' '.join(line['text'] for line in chosen)
    else:
        chosen = []
        title = target if usable else ''
    if not chosen:
        return {'title': title, 'lines': []}
    return {'title': title, 'lines': chosen, 'bottom': max(line['bbox'][3] for line in chosen),
            'left': min(line['bbox'][0] for line in chosen), 'right': max(line['bbox'][2] for line in chosen)}


def _title_box(raw, blocks, lines, page_height, title):
    evidence = title_evidence({**raw, 'Title': title or raw.get('Title')}, blocks, lines, page_height)
    if evidence['lines']:
        return evidence['bottom'], evidence['left'], evidence['right']
    headings = []
    for block in blocks:
        box = list(block.get('bbox') or [])
        if block.get('kind') != 'heading' or len(box) != 4 or not 15 < len(block.get('text', '')) < 600:
            continue
        if box[3] <= 1:
            # Source block coordinates are normalized; lines are in PDF points.
            # Keep X in its original coordinate system until matching PDF lines.
            box[1], box[3] = box[1] * page_height, box[3] * page_height
        if box[1] < page_height * .07 or box[1] > page_height * .4:
            continue
        headings.append((block['text'], box))
    target = _normalized(raw.get('Title') or title)
    matched = [(text, box) for text, box in headings if target and
               (_normalized(text) in target or SequenceMatcher(None, _normalized(text), target).ratio() >= .8)]
    if not matched and headings:
        # When Title metadata is absent, prefer the largest heading, rather than a section label.
        def size(entry):
            text, box = entry
            return max((line.get('size', 0) for line in lines if
                        abs(line['bbox'][1] - box[1]) < 3 and _normalized(line['text']) in _normalized(text)), default=0)
        first = max(headings, key=size)
        largest = size(first)
        matched = [entry for entry in headings if largest and size(entry) >= largest * .9
                   and 0 <= entry[1][1] - first[1][1] < page_height * .09
                   and abs(entry[1][0] - first[1][0]) < (.02 if first[1][2] <= 1 else 5)] or [first]
    if not matched:
        return None
    bottom = max(box[3] for _, box in matched)
    title_lines = [line for line in lines if line['bbox'][1] <= bottom + 1 and len(_normalized(line['text'])) > 15
                   and any(_normalized(line['text']) in _normalized(text) for text, _ in matched)]
    if not title_lines:
        return (bottom, None, None)
    return (bottom, min(line['bbox'][0] for line in title_lines), max(line['bbox'][2] for line in title_lines))


def extract(raw, blocks, lines, page_height, first_text='', footer_numbers=None, title=''):
    """Use first-page evidence; reference years and PDF timestamps are not publication dates."""
    texts = [str(line.get('text', '')) for line in lines]
    text = '\n'.join(texts) + '\n' + first_text
    out = {'journal': '', 'authors': [], 'doi': '', 'year': None, 'publication_date': '',
           'volume': '', 'issue': '', 'page_range': '', 'article_number': ''}
    subject = clean_venue(raw.get('Subject'))
    if 4 <= len(subject) <= 100 and not re.search(r'\b(?:abstract|keywords|arxiv)\b', subject, re.I):
        out['journal'] = subject
    headers = [line['text'] for line in lines if line.get('bbox', [0, page_height])[1] <= page_height * .09]
    # The plain text extractor may recover letter-spaced journal logos more faithfully.
    logo_headers = [s for s in first_text.splitlines()[:3] if '|' in s]
    for header in [*logo_headers, *headers]:
        match = re.search(r'^\s*(.+?)(?:,?\s+VOL(?:UME)?\.?\s|\s*\||\s+\d+\s*\()', header, re.I)
        name = clean_venue(match.group(1) if match else header)
        if 4 <= len(name) <= 100 and re.search(r'\b(?:Journal|Transactions|Letters|Proceedings|Nature|Science|Robotics|Sensors)\b', name, re.I):
            out['journal'] = name if is_conference_venue(name) else name.title().replace('Ieee', 'IEEE').replace('Asme', 'ASME')
            break
    # DOI searches are restricted to front matter and metadata, not reference lists.
    body_start = next((i for i, s in enumerate(texts) if re.match(r'^\s*(?:I\.?\s*)?INTRODUCTION\b', s, re.I)), len(texts))
    doi_text = '\n'.join(texts[:body_start]) + '\n' + '\n'.join(str(raw.get(k) or '') for k in ('Subject', 'Keywords', 'doi', 'DOI'))
    doi = _DOI.search(doi_text)
    if doi:
        out['doi'] = doi.group().rstrip('.,;:)')
    publication = re.search(r'(?:^|\n)\s*(?:Date of Publication|Published(?:\s+online)?|First published|Available online|Publication date)\s*[:—-]?\s*((?:\d|' + _MONTH + r')[^\n]{3,90})', text, re.I)
    if publication:
        out['publication_date'] = parse_date(publication.group(1))
    if not out['publication_date']:
        for header in headers:
            if out['journal'] and _normalized(out['journal']) in _normalized(header) and not re.search(r'\bX{2,}\b', header, re.I):
                found = _DATE.search(header)
                if found:
                    out['publication_date'] = parse_date(found.group())
                    break
    author_seed = str(raw.get('Author') or '').strip()
    # Visible bylines outrank PDF exporter metadata, which may contain a creator.
    title_box = _title_box(raw, blocks, lines, page_height, title)
    if title_box:
        bottom, left, right = title_box
        byline = sorted([line for line in lines if bottom - 1 <= line['bbox'][1] < bottom + page_height * .09
                         and (left is None or left - 5 <= line['bbox'][0] < right)], key=lambda l: (l['bbox'][1], l['bbox'][0]))
        for line in byline:
            if re.search(r'\b(?:Abstract|Index Terms|Keywords|INTRODUCTION)\b|摘\s*要|关键词', line['text'], re.I):
                break
            names = _names(line['text'])
            if names:
                out['authors'].extend(names)
            elif out['authors'] and len(line['text'].split()) > 7 and not _NON_AUTHOR.search(line['text']):
                # Unlabeled abstracts must stop the byline scan too.
                break
        out['authors'] = list(dict.fromkeys(out['authors']))
    seed_names = _names(author_seed)
    visible_names = {_normalized(name) for name in out['authors']}
    if seed_names and (not visible_names or (len(seed_names) > len(out['authors'])
            and visible_names.intersection(_normalized(name) for name in seed_names))):
        out['authors'] = seed_names
    header_text = '\n'.join(headers)
    for key, pattern in [('volume', r'\bVOL(?:UME)?\.?\s*(\d+)'), ('issue', r'\b(?:NO|ISSUE)\.?\s*(\d+)')]:
        match = re.search(pattern, header_text, re.I)
        if match:
            out[key] = match.group(1)
    page = re.search(r'\b(?:pp\.?|pages?)\s*[:.]?\s*(\d+\s*[-–—]\s*\d+)\b', header_text, re.I)
    if page:
        out['page_range'] = re.sub(r'\s*[-–—]\s*', '-', page.group(1))
    elif footer_numbers:
        first, last, count = footer_numbers
        if first and last and first > 1 and last - first + 1 == count:
            out['page_range'] = f'{first}-{last}'
    identifier = re.search(r'\b(?:Article\s+(?:number|no\.?|ID)|eLocator)\s*:?\s*((?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,})\b', text, re.I)
    if identifier:
        out['article_number'] = identifier.group(1)
    for footer in (line['text'] for line in lines if line['bbox'][1] > page_height * .9):
        # A publisher citation footer identifies an eLocator, not a PDF page counter.
        citation = re.search(r'\b(\d{1,4}),\s*(e[a-z0-9]{4,})\s*\(((?:19|20)\d{2})\)', footer, re.I)
        if citation:
            out['volume'] = out['volume'] or citation.group(1)
            out['article_number'] = out['article_number'] or citation.group(2)
            dates = [parse_date(match.group()) for match in _DATE.finditer(footer)]
            precise = max(dates, key=len, default='')
            if precise.startswith(citation.group(3)) and len(precise) > len(out['publication_date']):
                out['publication_date'] = precise
    if out['publication_date']:
        out['year'] = int(out['publication_date'][:4])
    arxiv = re.search(r'arXiv\s*:\s*(\d{4}\.\d{4,5})(?:v\d+)?', text, re.I)
    if not arxiv:
        reversed_id = re.search(r'(\d{4,5}\.\d{4})\s*:\s*viXra', text, re.I)
        if reversed_id:
            out['arxiv_id'] = reversed_id.group(1)[::-1]
    if arxiv:
        out['arxiv_id'] = arxiv.group(1)
    if is_conference_venue(out['journal']):
        out.update(paper_type='conference', conference_name=out['journal'],
                   conference_abbr=conference_abbreviation(out['journal']), journal='')
    out['metadata_provenance'] = {k: {'source': 'pdf', 'location': 'first-page'}
                                 for k in (*PUBLICATION_FIELDS, 'conference_abbr', 'paper_type') if out.get(k)}
    return out


def missing_fields(doc):
    doc = normalize_publication(doc)
    missing = []
    if not (doc.get('journal') or doc.get('conference_name')):
        missing.append({'key': 'journal', 'label': '期刊 / 出版来源'})
    if not (doc.get('publication_date') or doc.get('year')):
        missing.append({'key': 'publication_date', 'label': '发表时间'})
    if not doc.get('doi') or str(doc['doi']).lower().startswith('10.48550/arxiv.'):
        missing.append({'key': 'doi', 'label': '正式发表 DOI'})
    if not (doc.get('page_range') or doc.get('article_number')):
        missing.append({'key': 'page_range', 'label': '发表页码 / 文章编号'})
    if not valid_authors(doc.get('authors')):
        missing.append({'key': 'authors', 'label': '作者'})
    return missing
