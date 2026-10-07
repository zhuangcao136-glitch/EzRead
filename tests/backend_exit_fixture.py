"""Real backend/window exit fixture: temporary library, synthetic model processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

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


class Handler(OriginalHandler):
    def do_GET(self):
        if self.path == '/__test/lifetime':
            self.send_json({'attached': bool(server._lifecycle._watching)})
            return
        super().do_GET()
    def do_POST(self):
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
