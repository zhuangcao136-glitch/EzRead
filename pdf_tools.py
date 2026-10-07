"""Local PDF ingestion for EzRead. No network access or model calls.

Coordinates are normalized, top-left origin; page numbers are one-based.
PDF text/layout extraction is heuristic. The untouched PDF always remains the
source of truth, and difficult mathematical blocks include a rendered crop.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
import statistics
import threading
import unicodedata
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

_PDF_LOCK = threading.RLock()
_FIGURE_START = re.compile(r"^(?:Fig(?:ure)?|FIG(?:URE)?)\.?\s*\d+\s*(?:[|:—–]|\.(?=\s)|\s+(?=[A-Z]))")
_TABLE_START = re.compile(r"^(?:Table|TABLE)\s+\d+\b")
_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
_PROSE_LEAD = re.compile(r'^(?:where|and|or|that|which|with|for|if|when|then|using|here|as|we)\b', re.I)


def _box(obj):
    return [float(obj['x0']), float(obj['top']), float(obj['x1']), float(obj['bottom'])]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _norm(box, width, height):
    return [round(max(0, min(1, v / (width if i % 2 == 0 else height))), 6)
            for i, v in enumerate(box)]


def _intersect(a, b, padding=0):
    return (a[0] <= b[2] + padding and a[2] + padding >= b[0]
            and a[1] <= b[3] + padding and a[3] + padding >= b[1])


def _inside(cx, cy, box):
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _save_jpeg(image, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Caller holds the global lock; atomic replacement avoids partial files.
    temporary = path.with_suffix(path.suffix + '.partial')
    image.convert('RGB').save(temporary, format='JPEG', quality=90, optimize=True)
    temporary.replace(path)


def render_page(pdf_path, number, output_path, scale=1.5):
    """Render a one-based page. PDFium access is serialized across all threads."""
    number, scale = int(number), float(scale)
    if not math.isfinite(scale) or not 0.25 <= scale <= 4:
        raise ValueError('页面渲染比例须在 0.25 至 4 之间。')
    with _PDF_LOCK:
        document = pdfium.PdfDocument(str(pdf_path))
        try:
            if not 1 <= number <= len(document):
                raise ValueError('页码超出文献范围。')
            page = document[number - 1]
            try:
                width, height = page.get_size()
                safe_scale = min(scale, math.sqrt(32_000_000 / max(width * height, 1)))
                bitmap = page.render(scale=safe_scale)
                try:
                    _save_jpeg(bitmap.to_pil(), output_path)
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
    return str(output_path)


def _crop_page(asset_dir, page, bbox, filename, scale=2):
    asset_dir = Path(asset_dir)
    if len(bbox) != 4 or not all(math.isfinite(float(v)) for v in bbox):
        raise ValueError('裁剪坐标必须是四个有限数值。')
    x0, y0, x1, y1 = [max(0, min(1, float(v))) for v in bbox]
    if x1 - x0 < 0.004 or y1 - y0 < 0.004:
        raise ValueError('裁剪区域过小或坐标顺序有误。')
    with _PDF_LOCK:
        document = pdfium.PdfDocument(str(asset_dir / 'original.pdf'))
        try:
            if not 1 <= int(page) <= len(document):
                raise ValueError('页码超出文献范围。')
            pdfpage = document[int(page) - 1]
            try:
                width, height = pdfpage.get_size()
                safe_scale = min(scale, math.sqrt(32_000_000 / max(width * height, 1)))
                bitmap = pdfpage.render(scale=safe_scale)
                try:
                    image = bitmap.to_pil()
                    box = (int(x0 * image.width), int(y0 * image.height),
                           int(math.ceil(x1 * image.width)), int(math.ceil(y1 * image.height)))
                    _save_jpeg(image.crop(box), asset_dir / filename)
                finally:
                    bitmap.close()
            finally:
                pdfpage.close()
        finally:
            document.close()
    return filename


def _crop_extracted_region(asset_dir, page, bbox, filename):
    """Give tiny detected math/table regions enough page area to render.

    Manual cover crops keep the stricter validation in ``_crop_page``. A PDF
    glyph may be narrower than that threshold even when its box is valid.
    """
    if len(bbox) != 4 or not all(math.isfinite(float(value)) for value in bbox):
        return None
    x0, y0, x1, y1 = (max(0, min(1, float(value))) for value in bbox)
    if x1 <= x0 or y1 <= y0:
        return None
    def padded(start, end):
        if end - start >= .008:
            return start, end
        middle = (start + end) / 2
        start = max(0, min(middle - .004, 1 - .008))
        return start, start + .008
    x0, x1 = padded(x0, x1)
    y0, y1 = padded(y0, y1)
    return _crop_page(asset_dir, page, [x0, y0, x1, y1], filename)


def cover_crop(asset_dir, page, bbox):
    """Crop any original page into a persistent, content-addressed cover image."""
    fingerprint = hashlib.sha256(f'{int(page)}:{list(bbox)}'.encode()).hexdigest()[:12]
    return _crop_page(asset_dir, int(page), bbox, f'cover-{fingerprint}.jpg')


def cover_crop_figure(asset_dir, figure, bbox):
    """Crop within a gallery image, then render that region from the source PDF."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError('裁剪范围无效。')
    try:
        x0, y0, x1, y1 = (float(value) for value in bbox)
        fx0, fy0, fx1, fy1 = (float(value) for value in figure['bbox'])
        page = int(figure['page'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('图片裁剪信息无效。') from exc
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in
               (x0, y0, x1, y1, fx0, fy0, fx1, fy1)) or x1 - x0 < .015 or y1 - y0 < .015 or fx1 <= fx0 or fy1 <= fy0:
        raise ValueError('裁剪范围无效。')
    page_box = [fx0 + x0 * (fx1 - fx0), fy0 + y0 * (fy1 - fy0),
                fx0 + x1 * (fx1 - fx0), fy0 + y1 * (fy1 - fy0)]
    return cover_crop(asset_dir, page, page_box)


def _line_text(chars):
    """Restore spaces geometrically, including PDFs without actual space glyphs."""
    chars = sorted(chars, key=lambda c: (c['x0'], c['top']))
    parts, previous = [], None
    for char in chars:
        value = unicodedata.normalize('NFKC', char.get('text', ''))
        if not value:
            continue
        if previous is not None:
            gap = char['x0'] - previous['x1']
            threshold = max(0.7, min(char['size'], previous['size']) * 0.14)
            if gap > threshold and parts and not parts[-1].endswith(' ') and not value.startswith(' '):
                parts.append(' ')
        parts.append(value)
        previous = char
    return re.sub(r'[ \t]+', ' ', ''.join(parts)).strip()


def _column_gutter(page):
    """Find persistent whitespace between text columns, independent of row Y.

    Rows in the two columns need not share a baseline (e.g. closing notices).
    A central strip used by full-width abstracts is allowed only when its text
    density remains much lower than the adjacent column text.
    """
    chars = [c for c in page.chars if c.get('upright', True) and c.get('text', '').strip()
             and 35 < c['top'] < page.height - 30]
    if len(chars) < 200:
        return None
    values = [(x, sum(c['x0'] <= x < c['x1'] for c in chars))
              for x in range(int(page.width * .43), int(page.width * .57))]
    minimum = min(count for _, count in values)
    flank = statistics.median(count for x, count in values
                              if x < page.width * .46 or x > page.width * .54)
    if flank < 3 or minimum > flank * .72:
        return None
    threshold = minimum + int(minimum * .23)
    runs = []
    for x, count in values:
        if count <= threshold:
            if runs and x == runs[-1][-1] + 1:
                runs[-1].append(x)
            else:
                runs.append([x])
    runs = [r for r in runs if len(r) >= 5]
    if not runs:
        return None
    center = (min(c['x0'] for c in chars) + max(c['x1'] for c in chars)) / 2
    chosen = min(runs, key=lambda r: (0 if r[0] <= center <= r[-1] else min(abs(center-r[0]), abs(center-r[-1])), -len(r)))
    midpoint = min(chosen[-1] - 2, max(chosen[0] + 2, center))
    return [max(chosen[0], midpoint - 4), min(chosen[-1], midpoint + 4)]


def _lines(page):
    chars = [c for c in page.chars if c.get('upright', True) and c.get('text', '').strip()
             and 0 <= c['x0'] < page.width and 0 <= c['top'] < page.height]
    gutter = _column_gutter(page)
    if gutter:
        split = sum(gutter) / 2
        crosses = [c for c in chars if c['x0'] < gutter[1] and c['x1'] > gutter[0]]
        bands = [(float(c['matrix'][5]), float(c['size'])) for c in crosses if c.get('matrix')]
        separated = []
        for char in chars:
            baseline = float(char['matrix'][5]) if char.get('matrix') else page.height - char['bottom']
            spanning = any(abs(baseline - y) <= max(2, min(size, char['size']) * .28) for y, size in bands)
            column = 'span' if spanning else ('left' if (char['x0'] + char['x1']) / 2 < split else 'right')
            separated.append(dict(char, _column=column))
        chars = separated
    else:
        chars = [dict(c, _column='single') for c in chars]
    rows = []
    for char in sorted(chars, key=lambda c: (c['_column'], (c['top'] + c['bottom']) / 2, c['x0'])):
        cy = (char['top'] + char['bottom']) / 2
        candidates = [r for r in rows[-5:]
                      if r['column'] == char['_column'] and (abs(r['cy'] - cy) <= max(2.4, min(r['size'], char['size']) * 0.33)
                      or (min(r['bottom'], char['bottom']) - max(r['top'], char['top'])
                          > min(r['size'], char['size']) * .5
                          and abs(r['cy'] - cy) <= max(4, min(r['size'], char['size']) * .65)))]
        if candidates:
            row = min(candidates, key=lambda r: abs(r['cy'] - cy))
            row['chars'].append(char)
            if char['size'] >= row['size'] * 0.9:
                row['cy'] = statistics.median((c['top'] + c['bottom']) / 2 for c in row['chars'])
            row['size'] = max(row['size'], char['size'])
            row['top'] = min(row['top'], char['top'])
            row['bottom'] = max(row['bottom'], char['bottom'])
        else:
            rows.append({'cy': cy, 'size': char['size'], 'chars': [char],
                         'top': char['top'], 'bottom': char['bottom'], 'column': char['_column']})
    lines = []
    for row in rows:
        ordered = sorted(row['chars'], key=lambda c: c['x0'])
        segments = [[]]
        for char in ordered:
            if segments[-1]:
                prior = segments[-1][-1]
                # Split columns/cells, but never a normal word gap.
                if char['x0'] - prior['x1'] > max(18, min(prior['size'], char['size']) * 2.5):
                    segments.append([])
            segments[-1].append(char)
        for segment in segments:
            if not segment:
                continue
            text = _line_text(segment)
            if not text:
                continue
            size = statistics.median(c['size'] for c in segment)
            fonts = [c.get('fontname', '') for c in segment]
            bold = sum(bool(re.search(r'Bold|\.B\b|Semibold|Demi', f, re.I)) for f in fonts) / len(fonts)
            math_chars = sum(bool(re.search(r'Math|Mth|Symbol|CMSY|CMMI|MTMI', f, re.I)) for f in fonts)
            lines.append({'text': text, 'bbox': _union([_box(c) for c in segment]),
                          'size': size, 'bold': bold, 'math': math_chars / len(fonts),
                          'chars': segment, '_column': row['column'],
                          '_split': split if gutter else None})
    return sorted(lines, key=lambda l: (l['bbox'][1], l['bbox'][0]))


def _is_running(line, width, height, page_number):
    text, box = line['text'], line['bbox']
    if re.match(r'^https?://(?:dx\.)?doi\.org/', text, re.I) and (
            (page_number == 1 and box[1] < height * .15) or box[3] < 35):
        return True
    if text == 'Article' and (box[1] < height * 0.15):
        return True
    if text.lower() in {'check for updates', 'checkforupdates'}:
        return True
    if box[1] > height - 30:
        return True
    if re.fullmatch(r'[\d\s;,:)(]+', text) and len(text) > 9:
        return True
    if page_number > 1 and box[3] < 35:
        return True
    return False


def _graphic_candidates(page, lines):
    """Combine raster and vector primitives, guided by figure-caption anchors."""
    width, height = float(page.width), float(page.height)
    primitives = []
    for obj in list(page.images) + list(page.rects) + list(page.curves) + list(page.lines):
        box = _box(obj)
        w, h = box[2] - box[0], box[3] - box[1]
        if box[1] < 32 or box[3] > height - 26 or w < 0 or h < 0:
            continue
        if w > width * 0.8 and h < 2:
            continue
        if w > width * 0.94 and h > height * 0.9:
            continue
        if w * h < 2 and max(w, h) < 10:
            continue
        primitives.append(box)
    components = []
    for box in primitives:
        merged = box
        changed = True
        while changed:
            changed = False
            kept = []
            for other in components:
                if _intersect(merged, other, 7):
                    merged = _union([merged, other])
                    changed = True
                else:
                    kept.append(other)
            components = kept
        components.append(merged)
    candidates = [b for b in components if b[2] - b[0] > 55 and b[3] - b[1] > 40
                  and (b[2] - b[0]) * (b[3] - b[1]) > width * height * 0.012]
    captions = [line for line in lines if _FIGURE_START.match(line['text'])]
    for caption in captions:
        cb = caption['bbox']
        above = [b for b in components if b[3] <= cb[1] + 5 and cb[1] - b[3] < 60
                 and b[2] >= cb[0] and b[0] <= cb[2] + 12]
        if above:
            nearest = max(above, key=lambda b: b[3])
            aligned = [b for b in components if b[3] <= cb[1] + 5 and b[3] >= nearest[1]
                       and b[1] <= nearest[3] and b[0] >= 20 and b[2] <= width - 20]
            merged = _union(aligned or [nearest])
            if merged[2] - merged[0] > 55 and merged[3] - merged[1] > 40:
                candidates.append(merged)
    # Prefer containing whole-figure regions over small inset duplicates.
    result = []
    for box in sorted(candidates, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1])):
        nearby_caption = any(0 <= l['bbox'][1] - box[3] < 100 for l in captions)
        raster_area = sum((i['x1'] - i['x0']) * (i['bottom'] - i['top']) for i in page.images
                          if _inside((i['x0'] + i['x1']) / 2, (i['top'] + i['bottom']) / 2, box))
        if not nearby_caption and raster_area < width * height * .03:
            continue
        if any(_inside((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, prior) for prior in result):
            continue
        result.append([max(0, box[0] - 3), max(0, box[1] - 3),
                       min(width, box[2] + 3), min(height, box[3] + 3)])
    # Caption detection misses stand-alone photographs and visual panels.
    # Include substantive raster images that are not already inside a figure.
    for item in page.images:
        box = _box(item)
        w, h = box[2] - box[0], box[3] - box[1]
        if (box[1] < 32 or box[3] > height - 26 or w < 35 or h < 35
                or w * h < width * height * .002
                or w > width * .94 and h > height * .9):
            continue
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        if any(_inside(*center, previous) for previous in result):
            continue
        result.append([max(0, box[0] - 3), max(0, box[1] - 3),
                       min(width, box[2] + 3), min(height, box[3] + 3)])
    return sorted(result, key=lambda b: (b[1], b[0]))


def _join_lines(lines):
    value = ''
    for line in lines:
        text = line['text'].strip()
        if value.endswith('-') and text and text[0].islower():
            value = value[:-1] + text
        else:
            value += (' ' if value else '') + text
    return value


def _paragraphs(lines, width, body_size):
    groups = []
    for line in lines:
        box = line['bbox']
        ordinary_words = re.findall(r'[A-Za-z]{3,}', re.sub(r'\(cid:\d+\)', '', line['text']))
        ordinary_words = [word for word in ordinary_words
                          if not re.fullmatch(r'sin|cos|tan|arctan|exp|log|lim|max|min|[DW]?wall', word)]
        line['equation'] = (('=' in line['text'] or line['math'] > .24
                             or '(cid:' in line['text'] or re.search(r'[∑∏∂∈≤≥]', line['text'])
                             or re.search(r'(?:ffi|fi){4,}', line['text']))
                            and len(ordinary_words) < 5 and len(line['text']) < 240
                            and not _PROSE_LEAD.match(line['text']))
        is_heading = (len(line['text']) < 160 and
                      (line['size'] > body_size * 1.10 or line['bold'] > 0.72))
        line['heading'] = is_heading
        if groups:
            previous = groups[-1][-1]
            pb = previous['bbox']
            gap = box[1] - pb[3]
            similar_size = abs(line['size'] - previous['size']) < max(0.65, body_size * .08)
            indent = box[0] - groups[-1][0]['bbox'][0]
            new_reference = bool(re.match(r'^\d{1,3}\.\s', line['text']))
            starts_caption = bool(_FIGURE_START.match(line['text']) or _TABLE_START.match(line['text']))
            start_new = (gap > max(5.5, previous['size'] * .7) or gap < -2
                         or not similar_size or line['heading'] != previous['heading']
                         or line['equation'] != previous.get('equation', False)
                         or (indent > 7 and previous['text'].endswith(('.', '!', '?')))
                         or starts_caption or new_reference)
            # Strong paragraph indentation often marks a boundary even if the
            # preceding paragraph ends in a citation rather than punctuation.
            if 7 < indent < 35 and abs(previous['bbox'][0] - groups[-1][0]['bbox'][0]) < 4:
                start_new = True
            def isolated_glyph(item):
                return len(item['text']) <= 8 and len(re.findall(r'[A-Za-z]{3,}', item['text'])) == 0
            if isolated_glyph(previous) != isolated_glyph(line):
                start_new = True
            if line['equation'] and previous.get('equation'):
                start_new = gap > max(9, previous['size']) or gap < -3
            if not start_new:
                groups[-1].append(line)
                continue
        groups.append([line])
    blocks = []
    for group in groups:
        text = _join_lines(group)
        kind = 'heading' if (all(x['heading'] for x in group)
                             and (len(text) < 200 or all(x['bold'] > .72 for x in group))) else 'paragraph'
        if _FIGURE_START.match(text) or _TABLE_START.match(text):
            kind = 'caption'
        math_ratio = sum(x['math'] * len(x['chars']) for x in group) / max(1, sum(len(x['chars']) for x in group))
        equation = any(x.get('equation') for x in group) or (math_ratio > .24 and len(re.findall(r'[A-Za-z]{3,}', text)) < 5)
        blocks.append({'text': text, 'kind': kind, 'bbox': _union([x['bbox'] for x in group]),
                       '_lines': group, 'is_formula': equation})
    return blocks


def _ordered_blocks(lines, width, body_size, figure_boxes):
    # Full-width figures and captions create horizontal reading zones. Within
    # each zone, preserve left-column then right-column reading order.
    content = [line for line in lines if not any(_inside(
        (line['bbox'][0] + line['bbox'][2]) / 2,
        (line['bbox'][1] + line['bbox'][3]) / 2, b) for b in figure_boxes)]
    if not content:
        return []
    splits = []
    for i, left in enumerate(content):
        lb = left['bbox']
        for right in content[i + 1:i + 7]:
            rb = right['bbox']
            if abs(lb[1] - rb[1]) < 4 and .30 * width < lb[2] < .58 * width and .42 * width < rb[0] < .70 * width:
                if 9 < rb[0] - lb[2] < 50:
                    splits.append((lb[2] + rb[0]) / 2)
    trusted_splits = [line['_split'] for line in content if line.get('_split') is not None]
    split = (statistics.median(trusted_splits) if trusted_splits else
             (statistics.median(splits) if len(splits) > 2 else width / 2))
    has_columns = len(splits) > 2 or ({'left', 'right'} <= {line.get('_column') for line in content})
    # Only actual full-width graphic/caption boundaries cut a two-column
    # page. Arbitrary whitespace gaps would interleave unrelated paragraphs.
    boundaries = []
    for fig in figure_boxes:
        if fig[2] - fig[0] < width * .55:
            continue
        captions = [l for l in content if _FIGURE_START.match(l['text'])
                    and -5 <= l['bbox'][1] - fig[3] < 70]
        if not captions:
            continue
        caption = min(captions, key=lambda x: x['bbox'][1])
        below = sorted([l for l in content if l['bbox'][1] >= caption['bbox'][1] - 3],
                       key=lambda x: x['bbox'][1])
        end = caption['bbox'][3]
        for line in below:
            if line['size'] > caption['size'] * 1.08 or line['bbox'][1] - end > 12:
                break
            end = max(end, line['bbox'][3])
        boundaries.extend([caption['bbox'][1] - 3, end + .5])
    cuts = sorted(set(boundaries))
    bands = [[] for _ in range(len(cuts) + 1)]
    for line in content:
        zone = sum(line['bbox'][1] >= cut for cut in cuts)
        bands[zone].append(line)
    bands = [band for band in bands if band]
    output = []
    for band in bands:
        if not has_columns:
            output.extend(_paragraphs(sorted(band, key=lambda x: (x['bbox'][1], x['bbox'][0])), width, body_size))
            continue
        spans = [x for x in band if x['bbox'][0] < split - 8 and x['bbox'][2] > split + 8]
        # A large spanning paragraph (abstract) is kept together; column rows
        # elsewhere are emitted by horizontal zones.
        span_groups = []
        for span in sorted(spans, key=lambda x: x['bbox'][1]):
            if span_groups and span['bbox'][1] - span_groups[-1][-1]['bbox'][3] < max(7.5, body_size):
                span_groups[-1].append(span)
            else:
                span_groups.append([span])
        remaining = [x for x in band if x not in spans]
        def emit_columns(items):
            left = [x for x in items if (x['bbox'][0] + x['bbox'][2]) / 2 < split]
            right = [x for x in items if x not in left]
            return (_paragraphs(sorted(left, key=lambda x: x['bbox'][1]), width, body_size) +
                    _paragraphs(sorted(right, key=lambda x: x['bbox'][1]), width, body_size))
        for group in span_groups:
            before = [x for x in remaining if x['bbox'][1] < group[0]['bbox'][1]]
            output.extend(emit_columns(before))
            remaining = [x for x in remaining if x not in before]
            output.extend(_paragraphs(group, width, body_size))
        output.extend(emit_columns(remaining))
    return output


def _merge_captions(blocks):
    """Join bold caption leads and their smaller-font column continuations."""
    output = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        index += 1
        if block['kind'] != 'caption':
            output.append(block)
            continue
        base_size = statistics.median(l['size'] for l in block['_lines'])
        group = [block]
        while index < len(blocks):
            candidate = blocks[index]
            size = statistics.median(l['size'] for l in candidate['_lines'])
            span = _union([b['bbox'] for b in group])
            if (candidate['kind'] in {'heading', 'caption'} or size > base_size * 1.08
                    or candidate['bbox'][1] - span[3] > max(10, base_size * 1.5)
                    or candidate['bbox'][3] < span[1] - 5):
                break
            group.append(candidate)
            index += 1
        block['text'] = _join_lines([{'text': b['text']} for b in group])
        block['bbox'] = _union([b['bbox'] for b in group])
        block['_lines'] = [line for b in group for line in b['_lines']]
        if _TABLE_START.match(block['text']):
            block['is_table'] = True
            rows = []
            for line in sorted(block['_lines'], key=lambda l: (l['bbox'][1], l['bbox'][0])):
                if rows and abs(line['bbox'][1] - rows[-1][0]['bbox'][1]) < 3:
                    rows[-1].append(line)
                else:
                    rows.append([line])
            # Tables are read row-wise, regardless of the surrounding two-column
            # article. The rendered table remains the authoritative alignment.
            block['text'] = '\n'.join(' | '.join(l['text'] for l in sorted(row, key=lambda l: l['bbox'][0]))
                                      for row in rows)
        output.append(block)
    return output


def _merge_formula_regions(blocks):
    """Keep fractions, scripts and equation numbers in one source-image block."""
    def fragment(block):
        cleaned = re.sub(r'\(cid:\d+\)', '', block['text'])
        return (len(cleaned) < 36 and len(re.findall(r'[A-Za-z]{3,}', cleaned)) < 3
                and not _PROSE_LEAD.match(cleaned))
    output, index = [], 0
    while index < len(blocks):
        block = blocks[index]
        upcoming_formula = False
        if fragment(block):
            for upcoming in blocks[index + 1:index + 6]:
                if (upcoming['bbox'][1] - block['bbox'][3] > 18
                        or block['bbox'][1] - upcoming['bbox'][3] > 18):
                    break
                if upcoming.get('is_formula'):
                    upcoming_formula = True
                    break
                if not fragment(upcoming):
                    break
        starts = block.get('is_formula') or upcoming_formula
        index += 1
        if not starts:
            output.append(block)
            continue
        group = [block]
        while index < len(blocks):
            candidate = blocks[index]
            box = _union([b['bbox'] for b in group])
            if (not candidate.get('is_formula') and not fragment(candidate)):
                break
            if candidate['bbox'][1] - box[3] > 15 or box[1] - candidate['bbox'][3] > 15:
                break
            group.append(candidate)
            index += 1
        if any(b.get('is_formula') for b in group):
            block['is_formula'] = True
            block['kind'] = 'paragraph'
            block['bbox'] = _union([b['bbox'] for b in group])
            block['_lines'] = [line for b in group for line in b['_lines']]
        output.append(block)
    return output


def _merge_frontmatter(blocks, lines, author_seed):
    """Keep the author byline and publication dates compact on the first page."""
    seed = next((line for line in lines if author_seed
                 and author_seed.casefold() in line['text'].casefold()), None)
    if not seed:
        return blocks
    byline = [line for line in lines if abs(line['bbox'][1] - seed['bbox'][1]) < 5
              and line['bbox'][0] >= seed['bbox'][0] - 2]
    if not byline:
        return blocks
    text = ' '.join(line['text'] for line in sorted(byline, key=lambda l: l['bbox'][0]))
    # Keep source affiliation and correspondence markers in the reading text.
    text = re.sub(r'\s+([,;])', r'\1', text)
    text = re.sub(r'\s+', ' ', text).strip(' ,;& ')
    box = _union([line['bbox'] for line in byline])
    selected = [b for b in blocks if abs(b['bbox'][1] - box[1]) < 5
                and b['bbox'][0] >= box[0] - 2 and b['bbox'][3] <= box[3] + 3]
    dates = [b for b in blocks if re.match(r'^(Received|Accepted|Published)\s*:', b['text'], re.I)
             and b['bbox'][1] < box[3] + 90]
    if not selected:
        return blocks
    first = min(blocks.index(b) for b in selected + dates)
    merged = {'text': text, 'kind': 'paragraph', 'bbox': box, '_lines': byline,
              'is_formula': False, 'frontmatter': True}
    additions = [merged]
    if dates:
        ordered_dates = sorted(dates, key=lambda b: b['bbox'][1])
        additions.append({'text': '; '.join(b['text'] for b in ordered_dates), 'kind': 'paragraph',
                          'bbox': _union([b['bbox'] for b in dates]),
                          '_lines': [line for b in ordered_dates for line in b['_lines']],
                          'is_formula': False, 'frontmatter': True})
    result = []
    for index, block in enumerate(blocks):
        if index == first:
            result.extend(additions)
        if block not in selected and block not in dates:
            result.append(block)
    bottom = max(line['bbox'][3] for line in lines)
    affiliation = next((b for b in result if b['bbox'][1] > bottom * .84
                        and len(re.findall(r'\bUniversity|\bInstitute|\bDepartment|\bFaculty|\bLaboratory|\bLab\b', b['text'], re.I)) >= 2), None)
    if affiliation:
        y = affiliation['bbox'][1]
        affiliation_blocks = [b for b in result if y - 6 <= b['bbox'][1] <= y + 35]
        chars = [c for b in affiliation_blocks for line in b['_lines'] for c in line['chars']]
        # Sort two physical affiliation rows, bringing their superscript unit
        # labels and the e-mail continuation back into reading order.
        rows = []
        for char in sorted(chars, key=lambda c: ((c['top'] + c['bottom']) / 2, c['x0'])):
            cy = (char['top'] + char['bottom']) / 2
            if rows and abs(cy - statistics.median((c['top'] + c['bottom']) / 2 for c in rows[-1])) < 5:
                rows[-1].append(char)
            else:
                rows.append([char])
        entry = {'text': ' '.join(_line_text(row) for row in rows), 'kind': 'paragraph',
                 'bbox': _union([b['bbox'] for b in affiliation_blocks]),
                 '_lines': [line for b in affiliation_blocks for line in b['_lines']],
                 'is_formula': False, 'frontmatter': True}
        result = [b for b in result if b not in affiliation_blocks] + [entry]
    return result


def _metadata(pdf, first_blocks, first_lines, source_path):
    raw = pdf.metadata or {}
    import paper_metadata
    evidence = paper_metadata.title_evidence(raw, first_blocks, first_lines, pdf.pages[0].height)
    title = evidence['title'] or Path(source_path).stem
    abstract = ''
    abstract_idx = next((i for i, b in enumerate(first_blocks) if re.match(r'^abstract\b', b['text'], re.I)), None)
    if abstract_idx is not None:
        candidate = first_blocks[abstract_idx]
        abstract = re.sub(r'^abstract\s*[:.—-]?\s*', '', candidate['text'], flags=re.I)
        if len(abstract) < 100 and abstract_idx + 1 < len(first_blocks):
            abstract = first_blocks[abstract_idx + 1]['text']
    else:
        # Many Nature papers use a prominent unlabeled abstract.
        paragraphs = [b for b in first_blocks if b['kind'] == 'paragraph' and len(b['text']) > 500
                      and b['bbox'][1] < 400]
        if paragraphs:
            abstract = paragraphs[0]['text']
    def page_number(page):
        candidates = [line['text'].strip() for line in _lines(page)
                      if line['bbox'][1] < page.height * .07 or line['bbox'][1] > page.height * .93]
        numbers = [int(s) for s in candidates if re.fullmatch(r'\d{1,6}', s)]
        return numbers[-1] if numbers else None
    publication = paper_metadata.extract(raw, first_blocks, first_lines, pdf.pages[0].height,
        first_text=pdf.pages[0].extract_text() or '',
        footer_numbers=(page_number(pdf.pages[0]), page_number(pdf.pages[-1]), len(pdf.pages)), title=title)
    publication['metadata_provenance']['title'] = ({'source': 'pdf', 'location': 'first-page'} if evidence['lines']
        else {'source': 'pdf-metadata', 'location': 'Title'} if evidence['title'] else {'source': 'filename'})
    return {'title': title, 'abstract': abstract, **publication}


def extract_document(source_path, asset_dir):
    """Copy a PDF, extract aligned text and figure crops, and return JSON data.

    Page images are named in the return value; only the first page and figure
    crops are rendered eagerly. Call render_page on demand for subsequent pages.
    Optional block keys `is_formula` and `image` preserve equation source crops.
    """
    source_path, asset_dir = Path(source_path), Path(asset_dir)
    if not source_path.is_file():
        raise FileNotFoundError('找不到待导入 PDF。')
    asset_dir.mkdir(parents=True, exist_ok=True)
    original = asset_dir / 'original.pdf'
    if source_path.resolve() != original.resolve():
        shutil.copy2(source_path, original)
    pages, blocks, figures, warnings = [], [], [], []
    first_blocks, first_lines = [], []
    references = False
    with pdfplumber.open(original) as pdf:
        if not pdf.pages:
            raise ValueError('PDF 没有可读取页面。')
        if len(pdf.pages) > 1000:
            raise ValueError('单份文献暂不支持超过 1000 页。')
        for number, page in enumerate(pdf.pages, 1):
            width, height = float(page.width), float(page.height)
            lines = _lines(page)
            visible_lines = [l for l in lines if not _is_running(l, width, height, number)]
            body_sizes = [round(c['size'], 1) for c in page.chars if c.get('text', '').isalpha() and 6 <= c['size'] <= 14]
            body_size = statistics.mode(body_sizes) if body_sizes else 9
            figure_boxes = _graphic_candidates(page, visible_lines)
            local = _merge_formula_regions(_merge_captions(_ordered_blocks(visible_lines, width, body_size, figure_boxes)))
            if number == 1:
                local = _merge_frontmatter(local, lines, str((pdf.metadata or {}).get('Author', '')).strip())
            if not local and not page.chars:
                warnings.append(f'第 {number} 页未检测到可选文字；扫描页需要 OCR，原图仍可阅读。')
            if number == 1:
                first_blocks, first_lines = local, lines
            for index, block in enumerate(local, 1):
                block['id'], block['page'] = f'p{number}-b{index}', number
                if re.fullmatch(r'References|Bibliography|Literature cited', block['text'], re.I):
                    references = True
                elif re.match(r'^(Acknowledgements|Acknowledgments|Author contributions|Competing interests|Additional information|Publisher.s note)\b', block['text'], re.I):
                    references = False
                if references and block['kind'] != 'heading':
                    block['kind'] = 'reference'
                box = _norm(block['bbox'], width, height)
                if block['is_formula']:
                    image = _crop_extracted_region(asset_dir, number, box, f'equation-{number:03}-{index:03}.jpg')
                    if image:
                        block['image'] = image
                        block['text'] = '[公式：请对照原文图像]'
                    else:
                        warnings.append(f'第 {number} 页有公式位于页面范围之外；请对照原 PDF。')
                elif '(cid:' in block['text']:
                    block['has_unparsed_math'] = True
                    image = _crop_extracted_region(asset_dir, number, box, f'math-source-{number:03}-{index:03}.jpg')
                    if image:
                        block['image'] = image
                    else:
                        warnings.append(f'第 {number} 页有数学符号位于页面范围之外；请对照原 PDF。')
                    block['text'] = re.sub(r'\(cid:\d+\)', '⟦原文数学符号⟧', block['text'])
                elif block.get('is_table'):
                    image = _crop_extracted_region(asset_dir, number, box, f'table-{number:03}-{index:03}.jpg')
                    if image:
                        block['image'] = image
                    else:
                        warnings.append(f'第 {number} 页有表格位于页面范围之外；请对照原 PDF。')
                block['bbox'] = box
                geometry = ','.join(str(round(v, 5)) for v in box)
                block['source_key'] = f'p{number}-g' + hashlib.sha256(geometry.encode()).hexdigest()[:12]
                block.pop('_lines', None)
                if not block['is_formula']:
                    block.pop('is_formula', None)
                blocks.append(block)
            for box in figure_boxes:
                candidate_captions = [b for b in local if b['kind'] == 'caption'
                                      and _FIGURE_START.match(b['text'])
                                      and b['bbox'][1] * height >= box[3] - 10
                                      and b['bbox'][1] * height - box[3] < 100]
                caption = candidate_captions[0]['text'] if candidate_captions else ''
                # Caption continuation may occupy the other column at the
                # same vertical position, so retain it as part of the caption.
                if candidate_captions:
                    cb = candidate_captions[0]['bbox']
                    continuation = [b for b in local if b not in candidate_captions
                                    and b['kind'] == 'paragraph'
                                    and abs(b['bbox'][1] - cb[1]) < .018
                                    and b['bbox'][0] > cb[0] + .2
                                    and b['bbox'][3] < cb[3] + .02]
                    if continuation:
                        caption += ' ' + ' '.join(b['text'] for b in continuation)
                        for b in continuation:
                            b['kind'] = 'caption'
                fid = len(figures) + 1
                name = f'figure-{fid:03}.jpg'
                normalized = _norm(box, width, height)
                if normalized[2] - normalized[0] < .004 or normalized[3] - normalized[1] < .004:
                    warnings.append(f'第 {number} 页有插图位于页面范围之外，已跳过该插图。')
                    continue
                _crop_page(asset_dir, number, normalized, name)
                keywords = len(re.findall(r'\b(?:design|overview|system|platform|robot|drone|apparatus|prototype|setup|structure|gripper)\b', caption, re.I))
                area = (box[2] - box[0]) * (box[3] - box[1]) / (width * height)
                score = 40 + min(keywords, 7) * 8 + min(area, .6) * 15 - number * 1.8
                if re.match(r'^Fig(?:ure)?\.?\s*1\b', caption, re.I):
                    score += 25
                if not caption:
                    score -= 18
                figures.append({'id': f'fig-{fid}', 'page': number, 'path': name,
                                'bbox': normalized, 'caption': caption, 'score': round(score, 2)})
            pages.append({'number': number, 'width': width, 'height': height,
                          'image': f'page-{number:03}.jpg'})
        metadata = _metadata(pdf, first_blocks, first_lines, source_path)
    render_page(original, 1, asset_dir / 'page-001.jpg')
    cover = max(figures, key=lambda f: f['score'])['path'] if figures else 'page-001.jpg'
    if not figures:
        warnings.append('未可靠检测到论文插图，暂以首页为封面；可在原文中查看图像。')
    return {'metadata': metadata, 'pages': pages, 'blocks': blocks, 'figures': figures,
            'cover': cover, 'warnings': warnings,
            'extraction': {'version': 4, 'method': 'gutter-aware-two-column', 'ocr': False}}
