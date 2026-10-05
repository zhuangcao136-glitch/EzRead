"""Manually audit the frozen catalogue against its pinned 2025 source tables.

Run with: python tests/audit_journal_tiers_sources.py
Requires network access; intentionally excluded from offline unittest discovery.
"""

import csv
import io
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / 'static' / 'journal_tiers_2025.csv'
PINNED_COMMIT = 'a5caae3954b49610dd5f357ca8d4a107abee829b'
SOURCE_PREFIX = ('https://github.com/yuzhounh/Authoritative-Journal-Classification/'
                 f'blob/{PINNED_COMMIT}/')
CORE_SUBJECTS = ('ROBOTICS', 'AUTOMATION & CONTROL SYSTEMS',
                 'INSTRUMENTS & INSTRUMENTATION', 'ENGINEERING, AEROSPACE',
                 'ENGINEERING, MECHANICAL', 'ENGINEERING, MANUFACTURING',
                 'IMAGING SCIENCE & PHOTOGRAPHIC TECHNOLOGY')


def source_rows(url):
    if not url.startswith(SOURCE_PREFIX):
        raise ValueError(f'分区来源未固定到审核版本：{url}')
    path = urllib.parse.unquote(url[len(SOURCE_PREFIX):])
    mirror = ('https://cdn.jsdelivr.net/gh/yuzhounh/'
              f'Authoritative-Journal-Classification@{PINNED_COMMIT}/'
              + urllib.parse.quote(path))
    request = urllib.request.Request(mirror, headers={'User-Agent': 'EzRead-journal-tier-audit'})
    with urllib.request.urlopen(request, timeout=15) as response:
        content = response.read().decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(content)))


def main():
    with CATALOGUE.open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    sources = {row['source_url'] for row in rows if 'github.com' in row['source_url']}
    tables = {url: source_rows(url) for url in sources}
    source_by_issn = {}
    for table in tables.values():
        for item in table:
            for issn in item['ISSN/EISSN'].split('/'):
                if issn.strip():
                    source_by_issn.setdefault(issn.strip().upper(), []).append(item)
    errors = []
    checked = 0
    catalogue_issns = {row['issn'].upper() for row in rows}
    for table in tables.values():
        for item in table:
            subjects = [item.get(f'小类{i}', '') for i in range(1, 7)]
            if not any(any(subject in value for subject in CORE_SUBJECTS)
                       for value in subjects):
                continue
            zones = {item.get(f'小类{i}分区', '').strip().split(' ', 1)[0]
                     for i in range(1, 7)}
            if item['Top'].strip() != '是' and not zones & {'1', '2', '3'}:
                continue
            issns = {value.strip().upper() for value in item['ISSN/EISSN'].split('/')}
            if not issns & catalogue_issns:
                errors.append(f"核心学科漏收：{item['Journal']} ({item['ISSN/EISSN']})")
    for row in rows:
        if row['source_url'] not in tables:
            if row['tier'] == 'important':
                for item in source_by_issn.get(row['issn'].upper(), []):
                    zones = {value.strip().split(' ', 1)[0]
                             for key, value in item.items()
                             if re.fullmatch(r'小类\d+分区', key) and value and value.strip()}
                    if item['Top'].strip() == '是' or '1' in zones:
                        errors.append(f"{row['issn']} {row['name']}: 2019 重要名单与 2025 顶级条件冲突")
            continue
        checked += 1
        matches = [item for item in tables[row['source_url']]
                   if row['issn'].upper() in
                   {value.strip().upper() for value in item['ISSN/EISSN'].split('/')}]
        if len(matches) != 1:
            errors.append(f"{row['issn']} {row['name']}: 源表匹配到 {len(matches)} 条")
            continue
        item = matches[0]
        zones = {value.strip().split(' ', 1)[0]
                 for key, value in item.items()
                 if re.fullmatch(r'小类\d+分区', key) and value and value.strip()}
        is_top = item['Top'].strip() == '是'
        expected = ('top' if is_top or '1' in zones else
                    'important' if zones & {'2', '3'} else 'other')
        if row['tier'] != expected:
            errors.append(f"{row['issn']} {row['name']}: 名单={row['tier']}，源表={expected}")
        basis = row['basis']
        if (basis.endswith('大类Top') and not is_top or
                basis.endswith('小类1区') and '1' not in zones or
                basis.endswith('小类2或3区') and not zones & {'2', '3'}):
            errors.append(f"{row['issn']} {row['name']}: 来源依据与源表不符")
        if item['年份'] != '2025':
            errors.append(f"{row['issn']} {row['name']}: 源表年份不是 2025")
    print(f'核对 {len(tables)} 个源表、{checked} 条分区依据记录：{len(errors)} 项错误')
    if errors:
        raise SystemExit('\n'.join(errors))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
