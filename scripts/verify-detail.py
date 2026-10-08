"""Verify the detail layout with a temporary library, synthetic papers and no model calls."""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server
import codex_bridge
import codex_models
import codex_usage
from PIL import Image, ImageDraw

PID = '0123456789abcdef'


def main():
    work = ROOT / 'work'; work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='detail-layout-', dir=work, ignore_cleanup_errors=True) as folder:
        server.DATA = Path(folder).resolve(); server.LIBRARY = server.DATA / 'library'
        assert server.DATA.is_relative_to(work.resolve())
        server.init_db()
        server.codex_status = lambda **kwargs: {'available': False, 'authenticated': False}
        codex_bridge.prepare_selection = lambda **kwargs: None
        codex_models.get = lambda **kwargs: {'models': [], 'status': 'unavailable', 'refreshing': False}
        codex_usage.snapshot = codex_usage.get = lambda **kwargs: {'status': 'unavailable', 'buckets': []}
        doc = {'id': PID, 'hash': 'synthetic-detail-layout', 'title': 'An integrated tactile sensing system for soft robotic grasping, learning-based object recognition and force-controlled manipulation',
               'authors': ['Test Author', 'Second Author'], 'journal': 'The International Journal of Robotics Research', 'year': 2026, 'paper_type': 'journal',
               'doi': '10.1234/detail-fixture', 'publication_date': '2026-01-01', 'volume': '1', 'issue': '1', 'page_range': '1-20',
               'metadata_source': 'https://example.org/publication/detail-fixture',
               'metadata_enrichment': {'status': 'declined', 'checked_at': '2026-01-02T00:00:00+00:00', 'missing': [], 'conflicts': [], 'error': ''},
               'deleted': False, 'pages': [{'number': number, 'image': 'page-001.png', 'width': 800, 'height': 440} for number in range(1, 12)],
               'figures': [{'id': index, 'path': 'page-001.png', 'page': index} for index in range(1, 5)],
               'tags': ['柔性触觉传感', '抗冻水凝胶', '电容反馈', '物体识别', '闭环抓取控制', '滑移检测与补偿'],
               'blocks': [{'id': 'one', 'text': 'Synthetic source.'}],
               'translation': {'status': 'idle'}, 'notes': '保留的个人论文笔记。', 'team': '保留的历史团队资料', 'team_status': 'completed',
               'summary': '本文以可集成的柔性传感器为研究对象，描述了装置构型、实验方法和抓取反馈过程。',
               'problem': '在保持柔性的同时测量形变与接触信息，并将测量结果用于物体识别与精细操作。',
               'method': '采用可重复的标定流程，比较不同载荷与接触条件下的响应。\n使用闭环控制验证反馈信号的实际作用。',
               'results': '装置可在多种接触条件下提供稳定的反馈。' * 12,
               'limitations': '实验结论限于文中报告的条件，未报告的性能不作推断。'}
        server.put_doc(doc)
        image_dir = server.LIBRARY / PID; image_dir.mkdir(parents=True)
        figure = Image.new('RGB', (800, 440), '#f7f9f9')
        drawing = ImageDraw.Draw(figure)
        for left in (160, 340, 520):
            drawing.rounded_rectangle((left, 65, left + 100, 315), radius=30, fill='#d0e6df', outline='#537b6b', width=4)
        drawing.ellipse((300, 190, 500, 390), fill='#e5edf1', outline='#607c8b', width=4)
        figure.save(image_dir / 'page-001.png')
        server.update_doc(PID, lambda current: current.update(cover='page-001.png'))
        with server.db() as con:
            con.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('collections',?)", (json.dumps(['触觉传感', '软体机器人']),))
        class Handler(server.Handler):
            def log_message(self, *args): pass
            def do_GET(self):
                if self.path == '/__test/detail-state':
                    self.send_json(server.get_doc(PID)); return
                super().do_GET()
        listener = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.PORT = listener.server_port
        thread = threading.Thread(target=listener.serve_forever, daemon=True); thread.start()
        environment = dict(os.environ, EZREAD_TEST_URL=f'http://127.0.0.1:{listener.server_port}', EZREAD_TEST_ROOT=str(ROOT))
        runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
        node = environment.get('EZREAD_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
        environment.setdefault('EZREAD_PLAYWRIGHT', str(runtime / 'node_modules/playwright'))
        try:
            result = subprocess.run([node, str(ROOT / 'tests/browser_detail.cjs')], env=environment, cwd=ROOT)
            if result.returncode: raise SystemExit(result.returncode)
        finally:
            listener.shutdown(); thread.join(1); listener.server_close()
    print('Detail browser verification completed; real library writes and real model calls: 0.')


if __name__ == '__main__': main()
