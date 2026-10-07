"""Loopback HTTP contract; delegate business operations to the application context."""
from __future__ import annotations
from .context import ApplicationContext
from . import journal_catalogue
import contextlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import traceback
import urllib.parse
import urllib.request
import zipfile
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler


def handler_for(app: ApplicationContext):
    class Handler(BaseHTTPRequestHandler):
        server_version = f'EzRead/{app.VERSION}'

        def log_message(self, fmt, *args):
            if '/api/status' not in str(args) and '/api/papers' not in str(args):
                print(fmt % args, flush=True)

        def safe_origin(self):
            host = self.headers.get('Host', '')
            if host not in (f'127.0.0.1:{app.PORT}', f'localhost:{app.PORT}'):
                return False
            origin = self.headers.get('Origin')
            return (not origin or origin in (f'http://127.0.0.1:{app.PORT}', f'http://localhost:{app.PORT}')) and self.headers.get('Sec-Fetch-Site') != 'cross-site'

        def send_json(self, value, code=200):
            raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
            try:
                self.send_response(code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # AbortController intentionally disconnects superseded requests.
                self.close_connection = True

        def read_json(self):
            size = int(self.headers.get('Content-Length', 0))
            if size > 4 * 1024 * 1024:
                raise ValueError('提交内容过大。')
            if 'application/json' not in self.headers.get('Content-Type', ''):
                raise ValueError('请求必须使用 JSON。')
            body = self.rfile.read(size)
            value = json.loads(body or b'{}')
            if not isinstance(value, dict):
                raise ValueError('无效的请求。')
            return value

        def send_file(self, path, attachment=None):
            import mimetypes
            if not path.is_file():
                self.send_json({'error': '文件不存在。'}, 404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if attachment:
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(attachment))
            self.end_headers()
            with path.open('rb') as f:
                shutil.copyfileobj(f, self.wfile)

        def do_GET(self):
            if not self.safe_origin():
                self.send_json({'error': '仅允许本机访问。'}, 403)
                return
            try:
                parts = urllib.parse.urlparse(self.path)
                path = urllib.parse.unquote(parts.path)
                if path == '/api/health':
                    self.send_json({'app_root': str(app.ROOT), 'data_dir': str(app.DATA), 'pid': os.getpid(), 'version': app.VERSION})
                elif path == '/api/papers':
                    docs = app.all_docs()
                    collections = set(app.settings().get('collections', [])) | {d.get('collection') for d in docs if d.get('collection')}
                    catalogue = journal_catalogue.snapshot(app.DATA)
                    self.send_json({'papers': [app.public_doc(d) for d in docs], 'collections': sorted(collections),
                                    'journal_catalogue_revision': catalogue['revision'], 'journal_catalogue_error': catalogue['error']})
                elif path == '/api/status':
                    import codex_usage
                    self.send_json({'codex': app.codex_status(force=urllib.parse.parse_qs(parts.query).get('refresh') == ['1']), 'usage': codex_usage.snapshot(), 'queue': [{'paper_id': p, 'kind': k} for p, k in list(app.QUEUED)], 'version': app.VERSION, 'data_dir': str(app.DATA)})
                elif path == '/api/usage':
                    import codex_usage
                    self.send_json(codex_usage.get(refresh=urllib.parse.parse_qs(parts.query).get('refresh') == ['1']))
                elif path == '/api/settings':
                    self.send_json(app.settings())
                elif path == '/api/journal-catalogue':
                    self.send_json(journal_catalogue.snapshot(app.DATA))
                elif re.fullmatch(r'/api/journal-catalogue/proposals/[a-f0-9]{24}', path):
                    self.send_json(journal_catalogue.proposal(app.DATA, path.rsplit('/', 1)[-1]))
                elif path == '/api/models':
                    import codex_models
                    self.send_json(codex_models.get(refresh=urllib.parse.parse_qs(parts.query).get('refresh') == ['1']))
                elif path == '/api/trash':
                    self.send_json({'papers': [app.public_doc(d) for d in app.all_docs(True) if d.get('deleted')]})
                elif path == '/api/export':
                    export = app.DATA / 'tmp' / f'EzRead-backup-{int(time.time())}-{secrets.token_hex(3)}.zip'
                    snapshot = app.DATA / 'tmp' / f'backup-{secrets.token_hex(5)}.sqlite3'
                    with app.LOCK, app.db() as source, contextlib.closing(sqlite3.connect(snapshot)) as target:
                        source.backup(target)
                        docs = app.all_docs(include_deleted=True)
                    with zipfile.ZipFile(export, 'w', zipfile.ZIP_DEFLATED) as z:
                        z.write(snapshot, 'data/library.sqlite3')
                        z.writestr('library.json', json.dumps({'version': app.VERSION, 'papers': docs, 'settings': app.settings()}, ensure_ascii=False, indent=2))
                        z.writestr('data/journal_tiers.csv', journal_catalogue._encoded(journal_catalogue.snapshot(app.DATA)['rows']))
                        for file in app.LIBRARY.rglob('*'):
                            if file.is_file() and file.suffix.lower() in ('.pdf', '.jpg', '.png', '.json'):
                                z.write(file, 'data/library/' + file.relative_to(app.LIBRARY).as_posix())
                        z.writestr('RESTORE.txt', '关闭 EzRead 后，将本备份中的 data 文件夹复制到 EzRead 应用目录。原 data 文件夹请先另行备份。library.json 可用于其他软件读取。')
                    snapshot.unlink(missing_ok=True)
                    try:
                        self.send_file(export, 'EzRead-backup.zip')
                    finally:
                        export.unlink(missing_ok=True)
                elif re.fullmatch(r'/api/papers/[a-f0-9]{16}/translation-versions', path):
                    doc = app.get_doc(path.split('/')[-2])
                    current = app.version_metadata(app.translation_snapshot(doc))
                    current.update(id='current', current=True)
                    self.send_json({'versions': [current] + [app.version_metadata(v) for v in reversed(doc.get('translation_versions', []))],
                                    'drafts': [app.version_metadata(v) for v in reversed(doc.get('translation_drafts', []))]})
                elif re.fullmatch(r'/api/papers/[a-f0-9]{16}/ai', path):
                    import paper_ai
                    pid = path.split('/')[-2]
                    app.get_doc(pid)
                    raw_generation = urllib.parse.parse_qs(parts.query).get('generation', [None])[0]
                    generation = int(raw_generation) if raw_generation is not None and raw_generation.isdigit() else None
                    with app.LOCK, app.db() as con:
                        self.send_json(paper_ai.history(con, pid, generation))
                elif re.fullmatch(r'/api/papers/[a-f0-9]{16}', path):
                    self.send_json({'paper': app.public_doc(app.get_doc(path.split('/')[-1]), full=True)})
                elif path.startswith('/media/'):
                    components = path.split('/')
                    if len(components) < 4 or not re.fullmatch(r'[a-f0-9]{16}', components[2]):
                        raise ValueError('无效文件路径。')
                    pid, relative = components[2], '/'.join(components[3:])
                    app.get_doc(pid)
                    folder = (app.LIBRARY / pid).resolve()
                    target = (folder / relative).resolve()
                    if not target.is_relative_to(folder):
                        raise ValueError('无效文件路径。')
                    match = re.fullmatch(r'page-(\d+)\.jpg', relative)
                    if match and not target.exists():
                        import pdf_tools
                        number = int(match.group(1))
                        if not 1 <= number <= len(app.get_doc(pid).get('pages', [])):
                            raise ValueError('页码超出范围。')
                        pdf_tools.render_page(folder / 'original.pdf', number, target)
                    self.send_file(target)
                else:
                    relative = 'index.html' if path in ('/', '/index.html') else path.lstrip('/')
                    if relative.startswith('static/'):
                        relative = relative[7:]
                    target = (app.STATIC / relative).resolve()
                    if not target.is_relative_to(app.STATIC.resolve()):
                        raise ValueError('无效文件路径。')
                    self.send_file(target)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except ValueError as exc:
                self.send_json({'error': str(exc)}, 400)
            except Exception as exc:
                traceback.print_exc()
                self.send_json({'error': str(exc)}, 500)

        def do_POST(self):
            if not self.safe_origin():
                self.send_json({'error': '拒绝跨站请求。'}, 403)
                return
            try:
                path = urllib.parse.urlparse(self.path).path
                if path == '/api/journal-catalogue/proposals':
                    self.send_json(journal_catalogue.start_proposal(app, self.read_json().get('instruction')), 202)
                    return
                text_match = re.fullmatch(r'/api/papers/([a-f0-9]{16})/text-actions', path)
                if text_match:
                    from . import text_actions
                    from codex_bridge import TranslationError
                    pid = text_match.group(1)
                    data = self.read_json()
                    try:
                        if data.get('action') == 'cancel_translation':
                            self.send_json(app.SELECTION_REQUESTS.cancel(pid, data))
                        elif data.get('action') == 'translate_word':
                            self.send_json(text_actions.translate_word(app, pid, data))
                        elif data.get('action') in {'translate', 'translate_sentence'}:
                            self.send_json(text_actions.translate(app, pid, data))
                        else:
                            doc = text_actions.retranslate(app, pid, data) if data.get('action') == 'retranslate' else text_actions.apply(app, pid, data)
                            self.send_json({'paper': app.public_doc(doc, True)})
                    except text_actions.TextConflict as exc:
                        self.send_json({'error': str(exc)}, 409)
                    except TranslationError as exc:
                        code = 409 if exc.code == 'cancelled' else 408 if exc.code == 'timeout' else 502
                        self.send_json({'error': str(exc), 'code': exc.code}, code)
                    return
                file_match = re.fullmatch(r'/api/papers/([a-f0-9]{16})/(copy-pdf|reveal-pdf)', path)
                if file_match:
                    self.read_json()
                    self.send_json(app.paper_file_action(*file_match.groups()))
                    return
                ai_match = re.fullmatch(r'/api/papers/([a-f0-9]{16})/ai/(chat|model|highlight)', path)
                if ai_match:
                    import paper_ai
                    pid, action = ai_match.groups()
                    doc = app.get_doc(pid)
                    data = self.read_json()
                    if not isinstance(data, dict):
                        raise ValueError('请求内容无效。')
                    if action == 'model':
                        if set(data) - {'model','reasoning_effort'}:
                            raise ValueError('模型配置包含未知字段。')
                        with paper_ai.paper_lock(pid), app.LOCK, app.db() as con:
                            result = paper_ai.switch_model(con,pid,data,doc,app.LIBRARY)
                        self.send_json({'paper_ai': result})
                    elif action == 'chat':
                        if not app.codex_status().get('authenticated'):
                            raise ValueError('请先在官方 Codex 中登录 ChatGPT 账号。')
                        result = paper_ai.ask(app.db,pid,doc,data.get('question',''))
                        self.send_json(result)
                    else:
                        import paper_structure
                        block_id, selected = data.get('block_id'), data.get('text')
                        source = paper_structure.selection_source(doc, block_id, data.get('source_ids'))
                        if not isinstance(selected,str) or not 1<=len(selected.strip())<=1200:
                            raise ValueError('每次请选择不超过 1200 字符的连续正文。')
                        normalize = lambda s: ' '.join(s.split())
                        if normalize(selected) not in normalize(source):
                            raise ValueError('选中文字与该论文段落不一致。')
                        if not app.codex_status().get('authenticated'):
                            raise ValueError('请先在官方 Codex 中登录 ChatGPT 账号。')
                        import codex_models, codex_bridge
                        settings = app.settings()
                        config = codex_models.resolve_config(settings['selection_translation_model'], settings['selection_translation_reasoning_effort'])
                        result = codex_bridge.translate_selection(selected, **config)
                        self.send_json({'translation': result})
                    return
                if path == '/api/import':
                    size = int(self.headers.get('Content-Length', 0))
                    if not 0 < size <= 200 * 1024 * 1024:
                        raise ValueError('一次导入总大小请控制在 200 MB 以内。')
                    mime = self.headers.get('Content-Type', '')
                    if not mime.startswith('multipart/form-data'):
                        raise ValueError('请通过文件选择器上传 PDF。')
                    body = self.rfile.read(size)
                    message = BytesParser(policy=email_policy).parsebytes(('Content-Type: ' + mime + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
                    papers, errors = [], []
                    for part in message.iter_parts():
                        filename = part.get_filename()
                        if not filename:
                            continue
                        try:
                            if not filename.lower().endswith('.pdf'):
                                raise ValueError('仅支持 PDF 文件。')
                            papers.append(app.import_pdf(part.get_payload(decode=True), filename))
                        except Exception as exc:
                            errors.append({'filename': filename, 'error': str(exc)})
                    self.send_json({'papers': papers, 'errors': errors})
                    return
                match = re.fullmatch(r'/api/papers/([a-f0-9]{16})/(metadata-enrich|structure|translate|pause|summarize|team|cover|restore|translation-restore|translation-resume-draft)', path)
                if not match:
                    self.send_json({'error': '接口不存在。'}, 404)
                    return
                pid, action = match.groups()
                data = self.read_json()
                if action == 'metadata-enrich':
                    app.enrich_publication(pid, data)
                elif action == 'structure':
                    app.schedule_structure(pid)
                elif action in ('translate', 'summarize', 'team'):
                    state = app.codex_status()
                    if not state.get('authenticated'):
                        raise ValueError('请先通过 ChatGPT 账号登录本机 Codex。EzRead 不会使用 API 密钥。' + state.get('message', ''))
                    app.enqueue(pid, action, data if action == 'translate' else None)
                elif action == 'pause':
                    app.RESUME_REQUESTED.discard((pid, 'translate'))
                    app.CANCEL.setdefault((pid, 'translate'), threading.Event()).set()
                    app.update_doc(pid, lambda d: d['translation'].update(status='paused', error=''))
                elif action == 'restore':
                    app.update_doc(pid, lambda d: d.update(deleted=False))
                elif action == 'translation-restore':
                    app.restore_translation(pid, data.get('version_id'))
                elif action == 'translation-resume-draft':
                    app.restore_translation_draft(pid, data.get('draft_id'))
                elif action == 'cover':
                    doc = app.get_doc(pid)
                    figure = next((f for f in doc.get('figures', []) if f['id'] == data.get('figure_id')), None)
                    if not figure:
                        raise ValueError('请先选择已提取的论文图片。')
                    if 'bbox' in data:
                        import pdf_tools
                        cover = pdf_tools.cover_crop_figure(app.LIBRARY / pid, figure, data['bbox'])
                    else:
                        cover = figure['path']
                    app.update_doc(pid, lambda d: d.update(cover=str(cover)))
                self.send_json({'paper': app.public_doc(app.get_doc(pid), True)})
            except ValueError as exc:
                self.send_json({'error': str(exc)}, 400)
            except Exception as exc:
                traceback.print_exc()
                self.send_json({'error': str(exc)}, 500)

        def do_PATCH(self):
            if not self.safe_origin():
                self.send_json({'error': '拒绝跨站请求。'}, 403)
                return
            try:
                path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
                data = self.read_json()
                if path == '/api/journal-catalogue':
                    try:
                        result = journal_catalogue.apply_changes(app.DATA, data.get('changes'), data.get('revision'))
                    except journal_catalogue.CatalogueConflict as exc:
                        self.send_json({'error': str(exc)}, 409)
                        return
                    self.send_json(result)
                    return
                if path == '/api/settings':
                    data = app.validate_settings(data)
                    with app.LOCK, app.db() as con:
                        for k, v in data.items():
                            if k in app.DEFAULT_SETTINGS:
                                con.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (k, json.dumps(v)))
                    self.send_json(app.settings())
                    return
                match = re.fullmatch(r'/api/papers/([a-f0-9]{16})(?:/blocks/([^/]+))?', path)
                if not match:
                    raise ValueError('接口不存在。')
                pid, block_id = match.groups()
                if block_id:
                    for key in ('translation', 'note'):
                        if key in data and not isinstance(data[key], str):
                            raise ValueError('译文与批注必须是文字。')
                    if 'highlight' in data and not isinstance(data['highlight'], bool):
                        raise ValueError('高亮状态必须为布尔值。')
                    def apply_block(doc):
                        block = next((b for b in doc['blocks'] if b['id'] == block_id), None)
                        if not block:
                            raise ValueError('段落不存在。')
                        for k in ('translation', 'highlight', 'note'):
                            if k in data:
                                block[k] = data[k]
                        if 'translation' in data:
                            block['translation_manually_edited'] = True
                            block['translation_modified_at'] = app.now()
                    doc = app.update_doc(pid, apply_block)
                else:
                    retired_metrics = {'jif', 'jif_year', 'quartiles', 'metrics_source', 'conference_rankings'}
                    if retired_metrics.intersection(data):
                        raise ValueError('已取消影响因子、分区和会议评级的编辑。')
                    allowed = {'title', 'title_zh', 'summary', 'journal', 'journal_abbr', 'year', 'authors', 'tags', 'collection', 'favorite', 'notes', 'read_page', 'reader_state', 'last_read', 'doi', 'paper_type', 'conference_name', 'conference_abbr', 'conference_track', 'publication_date', 'volume', 'issue', 'page_range', 'article_number'}
                    fields = {k: v for k, v in data.items() if k in allowed}
                    for k in ('title', 'title_zh', 'summary', 'journal', 'journal_abbr', 'collection', 'notes', 'last_read', 'doi', 'paper_type', 'conference_name', 'conference_abbr', 'conference_track', 'publication_date', 'volume', 'issue', 'page_range', 'article_number'):
                        if k in fields and not isinstance(fields[k], str):
                            raise ValueError(k + ' 必须是文字。')
                    if 'favorite' in fields and not isinstance(fields['favorite'], bool):
                        raise ValueError('收藏状态必须为布尔值。')
                    if fields.get('publication_date') and not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', fields['publication_date']):
                        raise ValueError('发表时间请使用 YYYY、YYYY-MM 或 YYYY-MM-DD。')
                    if fields.get('publication_date'):
                        from datetime import date
                        pieces = [int(s) for s in fields['publication_date'].split('-')]
                        date(*[pieces[i] if i < len(pieces) else 1 for i in range(3)])
                        if fields.get('year') not in (None, '', pieces[0]):
                            raise ValueError('发表时间与年份不一致，请核对。')
                        fields['year'] = pieces[0]
                    if 'collection' in fields:
                        fields['collection'] = fields['collection'].strip()
                        if len(fields['collection']) > 100 or re.search(r'[\x00-\x1f]', fields['collection']):
                            raise ValueError('合集名称须为不超过 100 字的单行文字。')
                    if 'paper_type' in fields and fields['paper_type'] not in ('journal', 'conference', 'preprint', 'other'):
                        raise ValueError('无效文献类型。')
                    if 'conference_track' in fields and fields['conference_track'] not in ('main', 'workshop', 'unknown'):
                        raise ValueError('无效会议论文类型。')
                    for k in ('year',):
                        if k in fields:
                            if isinstance(fields[k], bool):
                                raise ValueError(k + ' 数值无效。')
                            if fields[k] not in (None, '') and not (isinstance(fields[k], int) or (isinstance(fields[k], str) and re.fullmatch(r'\d{4}', fields[k]))):
                                raise ValueError(k + ' 必须是整数年份。')
                            fields[k] = None if fields[k] in (None, '') else int(fields[k])
                    for k in ('year',):
                        if fields.get(k) is not None and not app.valid_year(fields[k]):
                            raise ValueError(k + ' 年份无效。')
                    for k in ('tags', 'authors'):
                        if k in fields and not isinstance(fields[k], list):
                            raise ValueError(k + ' 必须是列表。')
                    for k in ('tags', 'authors'):
                        if k in fields and any(not isinstance(v, str) for v in fields[k]):
                            raise ValueError(k + ' 必须是文字列表。')
                    if 'reader_state' in fields:
                        fields['reader_state'] = app.validate_reader_state(fields['reader_state'], app.get_doc(pid))
                        fields['read_page'] = fields['reader_state']['page']
                    if 'read_page' in fields:
                        fields['read_page'] = max(1, min(int(fields['read_page']), len(app.get_doc(pid)['pages'])))
                        fields['last_read'] = app.now()
                    doc = app.update_doc(pid, lambda d: app.patch_paper_fields(d, fields))
                self.send_json({'paper': app.public_doc(doc, True)})
            except (ValueError, TypeError, KeyError) as exc:
                self.send_json({'error': str(exc)}, 400)
            except Exception as exc:
                traceback.print_exc()
                self.send_json({'error': str(exc)}, 500)

        def do_DELETE(self):
            if not self.safe_origin():
                self.send_json({'error': '拒绝跨站请求。'}, 403)
                return
            try:
                match = re.fullmatch(r'/api/papers/([a-f0-9]{16})', urllib.parse.urlparse(self.path).path)
                if not match:
                    raise ValueError('接口不存在。')
                pid = match.group(1)
                for (paper_id, kind), event in list(app.CANCEL.items()):
                    if paper_id == pid:
                        event.set()
                        app.RESUME_REQUESTED.discard((paper_id, kind))
                app.update_doc(pid, lambda d: d.update(deleted=True))
                self.send_json({'ok': True, 'message': '已移入回收站。重新导入同一 PDF 可恢复所有笔记与译文。'})
            except ValueError as exc:
                self.send_json({'error': str(exc)}, 400)

    return Handler
