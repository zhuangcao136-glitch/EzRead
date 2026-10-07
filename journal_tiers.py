"""Frozen 2025 journal tiers for EzRead's five visual categories.

The catalogue is a curated, attributed subset of the final CAS table plus the
school's explicit 2019 list. It is a journal identity lookup, not a degree audit.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path


CATALOGUE = Path(__file__).resolve().parent / 'static' / 'journal_tiers_2025.csv'
CATALOGUE_YEAR = 2025


def _key(value):
    value = str(value or '').casefold().replace('&', 'and')
    return re.sub(r'[^a-z0-9\u3400-\u9fff]', '', value)


def _name_key(value):
    # Some imported PDFs append a citation, e.g. "Advanced Intelligent Systems 2024.6:2400022".
    name = str(value or '').strip()
    name = re.sub(r'\s+(?:19|20)\d{2}(?:[.,]\d+)?(?::\s*[\w.-]+)?\s*$', '', name)
    return _key(name)


def _issn(value):
    value = str(value or '').upper().strip()
    return value.replace('-', '') if re.fullmatch(r'[0-9]{4}-?[0-9]{3}[0-9X]', value) else ''


@lru_cache(maxsize=1)
def _catalogue():
    return catalogue_index(catalogue_rows())


def catalogue_index(rows):
    """Validate journal identities and build exact-match indexes."""
    by_name, by_issn = {}, {}
    for row in rows:
        if row['tier'] not in ('top', 'important', 'other') or not row['name'].strip():
            raise ValueError('期刊等级名单含有无效记录。')
        issn = _issn(row['issn'])
        if not re.fullmatch(r'[0-9]{7}[0-9X]', issn):
            raise ValueError(f"期刊 ISSN 无效：{row['name']}")
        checksum = sum((8 - i) * int(char) for i, char in enumerate(issn[:7]))
        checksum += 10 if issn[-1] == 'X' else int(issn[-1])
        if checksum % 11:
            raise ValueError(f"期刊 ISSN 校验失败：{row['name']}")
        if issn in by_issn:
            raise ValueError(f"期刊 ISSN 重复：{row['issn']}")
        for name in (row['name'], *(row['aliases'] or '').split(';')):
            key = _key(name)
            if not key:
                continue
            previous = by_name.get(key)
            if previous and previous['issn'] != row['issn']:
                raise ValueError(f'期刊别名重复：{name}')
            by_name[key] = row
        by_issn[issn] = row
    return by_name, by_issn


def classify(doc, *, index=None):
    paper_type = doc.get('paper_type', 'journal')
    if paper_type == 'conference':
        return {'tier': 'conference'}
    if paper_type == 'preprint':
        return {'tier': 'preprint'}
    if paper_type != 'journal':
        return {'tier': 'other'}
    by_name, by_issn = index if index is not None else _catalogue()
    issn = _issn(doc.get('journal_issn'))
    row = by_issn.get(issn)
    name_rows = [by_name[key] for value in (doc.get('journal'), doc.get('journal_abbr'))
                 if (key := _name_key(value)) and key in by_name]
    if len({item['issn'] for item in name_rows}) > 1 or (row and any(item['issn'] != row['issn'] for item in name_rows)):
        return {'tier': 'other', 'catalogue_year': CATALOGUE_YEAR,
                'tier_needs_review': True}
    if not row and name_rows:
        row = name_rows[0]
    if not row:
        return {'tier': 'other', 'catalogue_year': CATALOGUE_YEAR}
    return {'tier': row['tier'], 'catalogue_year': CATALOGUE_YEAR,
            'tier_journal': row['name'], 'tier_basis': row['basis'],
            'tier_source_url': row['source_url']}


def catalogue_rows():
    with CATALOGUE.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))
