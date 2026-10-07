"""Real browser selection events + real HTTP/storage, with synthetic papers/models."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import secrets
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server
import codex_models
import codex_usage
import codex_bridge
import paper_ai
import paper_structure

PID = '0123456789abcdef'
calls = []
slow_model = False


def main():
    native = '--native' in sys.argv
    run_id = secrets.token_hex(8)
    work = ROOT / 'work'; work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='reader-text-browser-', dir=work, ignore_cleanup_errors=True) as folder:
        data = Path(folder).resolve()
        assert data.is_relative_to(work.resolve())
        server.DATA, server.LIBRARY = data, data / 'library'
        server.init_db()
        server.codex_status = lambda **kwargs: {'authenticated': True, 'available': True}
        codex_models.get = lambda **kwargs: {'models': [], 'status': 'available'}
        codex_models.resolve_config = lambda *args, **kwargs: {'model': 'mock-model', 'reasoning_effort': 'low'}
        codex_usage.snapshot = codex_usage.get = lambda **kwargs: {'status': 'unavailable', 'buckets': []}
        def translate(text, **kwargs):
            record = {'text': text, 'target_language': kwargs.get('target_language'), 'cancelled': False}
            calls.append(record)
            if slow_model:
                event = kwargs.get('cancel_event')
                for _ in range(100):
                    if event is not None and event.wait(.05):
                        record['cancelled'] = True
                        raise codex_bridge.TranslationError('此翻译请求已取消。', code='cancelled')
            return '模拟划线译文：' + text
        codex_bridge.translate_selection = translate
        paper_ai.translate_batch = lambda *args, **kwargs: ('mock-thread', {b['id']: '重译：' + b['text'] for b in args[3]})
        doc = {'id': PID, 'hash': 'synthetic-original-pdf', 'title': 'Reader interaction fixture', 'authors': ['Test Author'],
               'journal': 'Synthetic Journal', 'year': 2026, 'deleted': False, 'translation': {'status': 'completed'},
               'pages': [{'number': 1, 'width': 595, 'height': 842, 'image': 'page-001.png'}], 'figures': [],
               'blocks': [
                   {'id': 'one', 'kind': 'paragraph', 'page': 1, 'text': 'First 😀 sentence. Another sentence.', 'translation': '第一😀句。第二句话保留。', 'bbox': [.1,.2,.85,.25]},
                   {'id': 'two', 'kind': 'paragraph', 'page': 1, 'text': 'Second paragraph has selectable text.', 'translation': '另一个自然段可以自由选择文字。', 'bbox': [.1,.4,.85,.45]},
                   {'id': 'three', 'kind': 'paragraph', 'page': 1, 'text': 'Legacy note and highlight stay available.', 'translation': '旧批注和高亮继续保留。', 'note': '原有批注', 'highlight': True, 'bbox': [.1,.6,.85,.65]},
                   {'id': 'four', 'kind': 'paragraph', 'page': 1, 'text': 'A long selected passage starts here. ' + 'Annotation geometry follows natural wrapping as the reader changes width and font size. ' * 5,
                    'translation': '批注应当跟随自然换行，不改变文字节点。' * 8, 'bbox': [.1,.7,.85,.8]},
                   {'id': 'five', 'kind': 'paragraph', 'page': 1, 'text': 'The same cross-paragraph annotation continues here.',
                    'translation': '这条跨段批注在下一段继续。', 'bbox': [.1,.8,.85,.9]}]}
        doc['reading_structure'] = {'version': paper_structure.VERSION, 'source_fingerprint': paper_structure.fingerprint(doc), 'status': 'ready',
            'sections': [{'id': 'intro', 'role': 'introduction', 'label': 'Introduction', 'entries': [{'id': b['id'], 'role': 'paragraph', 'source_ids': [b['id']]} for b in doc['blocks']]}]}
        server.put_doc(doc)
        from PIL import Image
        library = server.LIBRARY / PID; library.mkdir(parents=True)
        Image.new('RGB', (595, 842), '#f7f7ef').save(library / 'page-001.png')
        Image.new('RGB', (595, 842), '#f7f7ef').save(library / 'page-001.jpg')
        class Handler(server.Handler):
            def log_message(self, *args): pass
            def do_GET(self):
                if native and self.path == '/':
                    raw = (ROOT / 'static/index.html').read_text(encoding='utf-8').replace('</head>', '<script src="/__test/native-driver.js" defer></script></head>').encode('utf-8')
                    self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
                if native and self.path == '/__test/native-driver.js':
                    self.send_file(ROOT / 'tests/browser_reader_native.js'); return
                if self.path == '/__test/state':
                    self.send_json({'calls': calls, 'doc': server.get_doc(PID)}); return
                super().do_GET()
            def do_POST(self):
                global slow_model
                if self.path == '/__test/model-delay':
                    slow_model = bool(self.read_json().get('enabled'))
                    self.send_json({'ok': True}); return
                if self.path == '/__test/native-results':
                    (work / 'reader-text-webview2-verification.json').write_text(json.dumps({**self.read_json(), 'run_id': run_id},ensure_ascii=False,indent=2),encoding='utf-8')
                    self.send_json({'ok':True}); return
                if self.path == '/__test/append':
                    server.update_doc(PID, lambda d: d['blocks'][0].update(translation=d['blocks'][0]['translation'] + ' 后台更新。'))
                    self.send_json({'ok': True}); return
                super().do_POST()
        listener = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.PORT = listener.server_port
        thread = threading.Thread(target=listener.serve_forever, daemon=True); thread.start()
        environment = os.environ.copy()
        environment['EZREAD_TEST_URL'] = f'http://127.0.0.1:{listener.server_port}'
        environment['EZREAD_TEST_ROOT'] = str(ROOT)
        runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
        node = environment.get('EZREAD_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
        environment.setdefault('EZREAD_PLAYWRIGHT', str(runtime / 'node_modules/playwright'))
        try:
            if native:
                report = work / 'reader-text-native-host.json'
                command = [str(ROOT/'desktop/bin/EzRead.Desktop.exe'),'--app-root',str(ROOT),'--data-dir',str(data),'--url',environment['EZREAD_TEST_URL'],'--smoke-test',str(report)]
                result = subprocess.run(command,cwd=ROOT,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                value = json.loads((work / 'reader-text-webview2-verification.json').read_text(encoding='utf-8'))
                if value.get('run_id') != run_id: raise RuntimeError('Native reader verification did not finish in this run.')
                if 'error' in value: raise RuntimeError(value['error'])
                print(json.dumps(value,ensure_ascii=False))
            else:
                result = subprocess.run([node, str(ROOT / 'tests/browser_reader_text.cjs')], env=environment, cwd=ROOT)
            if result.returncode: raise SystemExit(result.returncode)
        finally:
            listener.shutdown(); listener.server_close(); thread.join()
    print('Browser selection verification completed; real library/model calls: 0.')


if __name__ == '__main__': main()
