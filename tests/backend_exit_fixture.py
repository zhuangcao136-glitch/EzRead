"""Real backend/window exit fixture: temporary library, synthetic model processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server
import codex_bridge
import codex_models
import codex_usage

PID = '0123456789abcdef'
assert server.DATA.resolve().is_relative_to((ROOT / 'work').resolve())
server.ROOT = Path(os.environ['EZREAD_TEST_APP_ROOT']).resolve()
assert server.ROOT.is_relative_to(server.DATA.resolve())
server.init_db()
if not server.all_docs():
    server.put_doc({'id': PID, 'hash': 'synthetic-exit-hash', 'title': 'Synthetic exit verification', 'authors': [], 'deleted': False,
                    'pages': [], 'figures': [], 'blocks': [{'id': 'one', 'text': 'Synthetic source.', 'translation': '已保存的测试译文'}],
                    'notes': '关闭前已保存的测试笔记', 'translation': {'status': 'idle'}})
server.codex_status = lambda **kwargs: {'available': False, 'authenticated': False}
codex_bridge.prepare_selection = lambda **kwargs: None
codex_bridge.status = server.codex_status
codex_models.get = lambda **kwargs: {'status': 'unavailable', 'models': [], 'refreshing': False}
codex_usage.get = codex_usage.snapshot = lambda **kwargs: {'status': 'unavailable', 'buckets': []}


def execute(pid, event):
    event.wait(60)
    server.update_doc(pid, lambda doc: doc['translation'].update(status='paused'))


server.execute_translation = execute
OriginalHandler = server.Handler
CLOSE_DELAY = int(os.environ.get('EZREAD_TEST_CLOSE_DELAY_MS', '0'))
CLOSE_FAIL_ONCE = os.environ.get('EZREAD_TEST_CLOSE_FAIL_ONCE') == '1'
CLOSE_NOTES = os.environ.get('EZREAD_TEST_CLOSE_NOTES', '关闭握手保存的测试笔记')
CLOSE_STATE = {'ready': False, 'attempts': []}
CLOSE_LOCK = threading.Lock()


def close_script():
    # Only the isolated fixture changes this response. Execute the production
    # hook after a deliberate delay so native visibility and saving are separate.
    return (ROOT / 'static/desktop.js').read_text(encoding='utf-8') + r'''
;(() => {
  const original = ezreadPrepareDesktopClose;
  const send = async (name, body) => {
    const response = await fetch('/__test/' + name, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!response.ok) throw new Error('Exit fixture did not acknowledge ' + name);
    return response.json();
  };
  ezreadPrepareDesktopClose = async function() {
    const probe = await send('close-start', {});
    await new Promise(resolve => setTimeout(resolve, probe.delay));
    const textarea = document.querySelector('.notes-area');
    textarea.value = probe.notes;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    // Leave this edit pending until the real close hook flushes it.
    clearTimeout(detailNoteSaves.get('0123456789abcdef').timer);
    const saved = await original();
    const result = saved && !probe.fail;
    await send('close-done', { attempt: probe.attempt, saved, result });
    return result;
  };
  window.addEventListener('load', async () => {
    while (state.loading || !state.papers.some(p => p.id === '0123456789abcdef')) {
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    await openDetail('0123456789abcdef');
    await send('ready', {});
  }, { once: true });
})();
'''


def persist_close_state():
    (server.DATA / 'close-state.json').write_text(json.dumps(CLOSE_STATE), encoding='utf-8')


class Handler(OriginalHandler):
    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/static/desktop.js' and CLOSE_DELAY:
            raw = close_script().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(raw)
            return
        if path == '/__test/close-state':
            with CLOSE_LOCK: self.send_json(CLOSE_STATE)
            return
        if self.path == '/__test/lifetime':
            self.send_json({'attached': bool(server._lifecycle._watching)})
            return
        super().do_GET()
    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if path in ('/__test/ready', '/__test/close-start', '/__test/close-done'):
            body = self.read_json()
            with CLOSE_LOCK:
                if path == '/__test/ready':
                    CLOSE_STATE['ready'] = True
                    answer = {}
                elif path == '/__test/close-start':
                    attempt = len(CLOSE_STATE['attempts']) + 1
                    notes = f'{CLOSE_NOTES}，第 {attempt} 次'
                    CLOSE_STATE['attempts'].append({'started': time.monotonic(), 'notes': notes})
                    answer = {'attempt': attempt, 'delay': CLOSE_DELAY, 'fail': CLOSE_FAIL_ONCE and attempt == 1, 'notes': notes}
                else:
                    entry = CLOSE_STATE['attempts'][body['attempt'] - 1]
                    entry.update(finished=time.monotonic(), saved=body['saved'], result=body['result'])
                    answer = {}
                persist_close_state()
                self.send_json(answer)
            return
        if self.path != '/__test/spawn':
            return super().do_POST()
        self.read_json()
        grandchild = 'import time; time.sleep(300)'
        child = ('import subprocess,sys,time,json; p=subprocess.Popen([sys.executable,"-c",' + repr(grandchild) + ']); '
                 'print(json.dumps({"grandchild_pid":p.pid}),flush=True); time.sleep(300)')
        process = codex_bridge.spawn([sys.executable, '-c', child], stdout=subprocess.PIPE,
                                     text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        value = json.loads(process.stdout.readline())
        event = server.CANCEL[(PID, 'translate')] = threading.Event()
        assert not event.is_set()
        server.QUEUED.add((PID, 'translate'))
        server.update_doc(PID, lambda doc: doc['translation'].update(status='running', staging={'one': '已保留的测试重译草稿'}))
        server.JOBS.put((PID, 'translate'))
        self.send_json({'child_pid': process.pid, **value})


server.Handler = Handler
server.main()
