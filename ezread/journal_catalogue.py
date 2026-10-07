"""Editable, hot-loaded journal catalogue shared by settings, HTTP and Codex CLI.

No paper records are rewritten: every public paper derives its tier from the
current validated catalogue. Invalid external edits retain the last valid view.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import csv
import hashlib
import io
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.parse

import journal_tiers

FIELDS = ('tier', 'name', 'issn', 'aliases', 'basis', 'source_url')
TIERS = ('top', 'important', 'other')
_LOCK = threading.RLock()
_CACHE = {}
_JOBS = {}


class CatalogueConflict(ValueError):
    pass


def catalogue_path(data_dir):
    return Path(data_dir) / 'journal_tiers.csv'


def normalize_rows(rows):
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError('期刊名单必须是最多 5000 条的列表。')
    clean = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - set(FIELDS):
            raise ValueError('期刊记录字段无效。')
        item = {}
        for field in FIELDS:
            value = row.get(field, '')
            if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 for c in value):
                raise ValueError(f'期刊 {field} 必须是不超过 2000 字的单行文字。')
            item[field] = value.strip()
        issn = journal_tiers._issn(item['issn'])
        item['issn'] = issn[:4] + '-' + issn[4:] if issn else item['issn']
        item['aliases'] = ';'.join(dict.fromkeys(v.strip() for v in item['aliases'].split(';') if v.strip()))
        url = urllib.parse.urlparse(item['source_url'])
        if item['source_url'] and (url.scheme not in ('http', 'https') or not url.netloc):
            raise ValueError('来源链接必须是 http 或 https 地址。')
        clean.append(item)
    journal_tiers.catalogue_index(clean)
    return sorted(clean, key=lambda r: (TIERS.index(r['tier']), r['name'].casefold(), r['issn']))


def _encoded(rows):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')


def _load(data_dir):
    path = catalogue_path(data_dir).resolve()
    with _LOCK:
        previous = _CACHE.get(path)
        try:
            raw = path.read_bytes() if path.exists() else journal_tiers.CATALOGUE.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if previous and previous['digest'] == digest:
                return previous, ''
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''))
            if reader.fieldnames != list(FIELDS):
                raise ValueError('CSV 表头必须为 ' + ','.join(FIELDS))
            rows = normalize_rows(list(reader))
            value = {'rows': rows, 'index': journal_tiers.catalogue_index(rows), 'digest': digest}
            _CACHE[path] = value
            return value, ''
        except (ValueError, UnicodeError, OSError) as exc:
            if previous is None:
                rows = normalize_rows(journal_tiers.catalogue_rows())
                previous = {'rows': rows, 'index': journal_tiers.catalogue_index(rows),
                            'digest': hashlib.sha256(_encoded(rows)).hexdigest()}
            return previous, f'可编辑名单读取失败，暂用上次有效名单：{exc}'


def snapshot(data_dir):
    value, error = _load(data_dir)
    return {'rows': copy.deepcopy(value['rows']), 'revision': value['digest'],
            'catalogue_year': journal_tiers.CATALOGUE_YEAR,
            'counts': {tier: sum(r['tier'] == tier for r in value['rows']) for tier in TIERS},
            'path': str(catalogue_path(data_dir)), 'error': error}


def classify(data_dir, doc):
    value, _ = _load(data_dir)
    return {**journal_tiers.classify(doc, index=value['index']), 'catalogue_revision': value['digest']}


def preview_changes(rows, changes):
    if not isinstance(changes, list) or not 1 <= len(changes) <= 500:
        raise ValueError('每次修改需要 1–500 条操作。')
    current = {row['issn']: dict(row) for row in rows}
    seen, diff = set(), []
    for change in changes:
        if not isinstance(change, dict) or change.get('action') not in ('upsert', 'delete'):
            raise ValueError('名单操作必须为 upsert 或 delete。')
        if set(change) - {'action', *FIELDS}:
            raise ValueError('名单操作含有未知字段。')
        raw_issn = change.get('issn')
        if not isinstance(raw_issn, str) or not (key := journal_tiers._issn(raw_issn)):
            raise ValueError('修改时必须提供有效 ISSN。')
        issn = key[:4] + '-' + key[4:]
        if issn in seen:
            raise ValueError('一次修改不能重复操作同一 ISSN。')
        seen.add(issn)
        before = current.get(issn)
        if change['action'] == 'delete':
            if before is None:
                raise ValueError(f'要移除的期刊不存在：{issn}')
            del current[issn]
            after = None
        else:
            after = {**(before or {}), **{k: v for k, v in change.items() if k in FIELDS}, 'issn': issn}
            after = normalize_rows([after])[0]
            current[issn] = after
        if before != after:
            diff.append({'before': before, 'after': after})
    return normalize_rows(list(current.values())), diff


def apply_changes(data_dir, changes, expected_revision):
    path = catalogue_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, _write_lock(path):
        before = snapshot(data_dir)
        if before['error']:
            raise ValueError(before['error'])
        if expected_revision != before['revision']:
            raise CatalogueConflict('名单已被其他操作修改，请刷新后再保存。')
        rows, diff = preview_changes(before['rows'], changes)
        if not diff:
            return {**before, 'changed': 0}
        path.parent.mkdir(parents=True, exist_ok=True)
        history = path.parent / 'journal-tier-history'
        history.mkdir(exist_ok=True)
        backup = history / f'{time.time_ns()}-{before["revision"][:12]}.csv'
        backup.write_bytes(path.read_bytes() if path.exists() else journal_tiers.CATALOGUE.read_bytes())
        temp = path.with_name(f'.journal-tiers-{secrets.token_hex(6)}.tmp')
        try:
            with temp.open('wb') as handle:
                handle.write(_encoded(rows))
                handle.flush()
                os.fsync(handle.fileno())
            # A direct file editor may have written while validation was running.
            if snapshot(data_dir)['revision'] != expected_revision:
                raise CatalogueConflict('名单已被其他操作修改，请刷新后再保存。')
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        return {**snapshot(data_dir), 'changed': len(diff)}


@contextmanager
def _write_lock(path):
    """Serialize CLI and HTTP writers across processes; OS releases on exit."""
    with path.with_suffix('.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic() + 3
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise CatalogueConflict('名单正在保存，请稍后重试。')
                    time.sleep(.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def start_proposal(app, instruction):
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4000:
        raise ValueError('请输入不超过 4000 字的名单修改要求。')
    base = snapshot(app.DATA)
    if base['error']:
        raise ValueError(base['error'])
    import codex_models
    config = codex_models.resolve_config(app.settings()['selection_translation_model'])
    scope = str(Path(app.DATA).resolve())
    with _LOCK:
        if any(job['scope'] == scope and job['status'] == 'running' for job in _JOBS.values()):
            raise ValueError('已有名单修改正在生成，请等待完成。')
        # Finished previews expire after an hour; no paper text or chat is stored.
        for key in list(_JOBS):
            if _JOBS[key]['status'] != 'running' and time.time() - _JOBS[key]['created'] > 3600:
                del _JOBS[key]
        job_id = secrets.token_hex(12)
        job = {'id': job_id, 'scope': scope, 'created': time.time(), 'status': 'running',
               'base_revision': base['revision'], 'model': config['model']}
        _JOBS[job_id] = job

    def run():
        import codex_bridge
        try:
            result = codex_bridge.propose_journal_changes(instruction.strip(), base['rows'], **config)
            changes = result.get('changes')
            if changes == []:
                diff = []
            else:
                _, diff = preview_changes(base['rows'], changes)
            with _LOCK:
                job.update(status='ready', changes=result['changes'], diff=diff,
                           summary=str(result.get('summary', ''))[:2000])
        except Exception as exc:
            with _LOCK:
                job.update(status='failed', error=str(exc))
    threading.Thread(target=run, name='journal-catalogue-proposal', daemon=True).start()
    return proposal(app.DATA, job_id)


def proposal(data_dir, job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job or job['scope'] != str(Path(data_dir).resolve()):
            raise ValueError('名单修改任务不存在，请重新生成。')
        return copy.deepcopy({k: v for k, v in job.items() if k not in ('scope', 'created')})
