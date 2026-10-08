"""Launch the real desktop shell against mocked HTTP APIs and a temporary profile.

No real papers, authentication, translation or Windows input automation. Requires
a built desktop shell and a Windows desktop session. Reports native window icon
handles and actual WebView2 document state; it does not photograph the taskbar.
"""
import functools
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    paper_count = 5
    def log_message(self, *args): pass

    def do_GET(self):
        name = self.path.split('?', 1)[0]
        if name.startswith('/api/'):
            paper = {'id': 'smoke-paper', 'title': 'WebView2 verification paper', 'authors': 'Test Author', 'journal': 'Test Journal', 'year': 2026, 'paper_type': 'journal', 'figures': [], 'blocks': [], 'pages': [], 'translation': {'status': 'idle'}}
            papers = [dict(paper, id='smoke-paper' if i == 0 else f'smoke-paper-{i}', title=f'Uniform scaling verification paper {i + 1}: methods and results') for i in range(self.paper_count)]
            catalogue = {'rows': [{'issn': '1234-5678', 'name': 'Test Journal', 'aliases': '', 'tier': 'other'}],
                         'counts': {'top': 0, 'important': 0, 'other': 1}, 'revision': 'fixture-catalogue', 'error': ''}
            value = {'/api/papers': {'papers': papers, 'collections': []}, '/api/settings': {'theme': 'sage'}, '/api/journal-catalogue': catalogue, '/api/models': {'models': []}, '/api/status': {'codex': {'available': False, 'authenticated': False}, 'queue': []}, '/api/usage': {'status': 'unavailable', 'buckets': []}}.get(name, {})
            content = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers(); self.wfile.write(content)
            return
        if name == '/': self.path = '/static/index.html'
        return super().do_GET()


def main():
    if os.name != 'nt': raise SystemExit('Native smoke checks require Windows.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path, default=ROOT / 'desktop/bin/EzRead.Desktop.exe')
    parser.add_argument('--background-stability', action='store_true')
    parser.add_argument('--expect-background-shift', action='store_true')
    options = parser.parse_args()
    executable = options.executable.resolve()
    Handler.paper_count = 40 if options.background_stability else 5
    if not executable.is_relative_to(ROOT.resolve()): raise SystemExit('Verification executable must stay inside the workspace.')
    handler = functools.partial(Handler, directory=str(ROOT))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    reports = []
    (ROOT / 'work').mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='webview2-smoke-', dir=ROOT / 'work', ignore_cleanup_errors=True) as folder:
            data = Path(folder)
            assert data.resolve().is_relative_to((ROOT / 'work').resolve())
            marker = data / 'webview2-migration.json'
            marker.write_text(json.dumps({'ezread-sort': 'year', 'ezread-reader-notes:smoke-paper': '{"value":"offline test draft"}'}), encoding='utf-8')
            for cycle in range(2):
                report = data / f'report-{cycle}.json'
                command = [str(executable), '--app-root', str(ROOT), '--data-dir', str(data), '--url', f'http://127.0.0.1:{server.server_port}', '--smoke-test', str(report)]
                process = subprocess.Popen(command, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
                if cycle == 0:
                    time.sleep(.7)
                    duplicate = subprocess.run(command, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                    assert duplicate.returncode == 0, 'Duplicate startup should activate the existing native window.'
                try:
                    code = process.wait(timeout=70)
                except subprocess.TimeoutExpired:
                    # Only the child created here against this temporary library.
                    process.terminate(); process.wait(timeout=5)
                    log = data / 'desktop.log'
                    if log.exists(): (ROOT / 'work' / 'webview2-smoke-failure.log').write_bytes(log.read_bytes())
                    raise RuntimeError('Native smoke timed out; inspect its isolated desktop.log.')
                if code != 0:
                    raise RuntimeError(report.read_text(encoding='utf-8-sig') if report.exists() else f'Native window exited with {code}')
                value = json.loads(report.read_text(encoding='utf-8-sig'))
                if options.background_stability:
                    (ROOT / 'work' / f'settings-background-native-{"before" if options.expect_background_shift else "verification"}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                assert value['bigIconMatchesBook'] and value['captionIconOwned'], value
                assert value['windowTitle'] == '\u200b', value
                assert value['page']['desktop'] and value['page']['body'] and value['page']['closeHook'], value
                assert value['page']['cards'] == Handler.paper_count, value
                layouts = value['layouts']
                assert len(layouts) == 3, value
                baseline = layouts[0]['layout']
                for item in layouts:
                    layout = item['layout']
                    assert abs(layout['viewport'] - 1920) <= 1, item
                    assert len(layout['tracks']) == 5, item
                    assert len({card['x'] for card in layout['cards']}) == 5, item
                    assert abs(layout['sidebar'] - baseline['sidebar']) <= 1, item
                    for card, original in zip(layout['cards'], baseline['cards']):
                        assert card['id'] == original['id'] and card['title'] == original['title'], item
                        if not options.background_stability: assert all(abs(card[key] - original[key]) <= 1 for key in ('x', 'y', 'width', 'height')), item
                assert layouts[0]['zoomFactor'] < layouts[1]['zoomFactor'] < layouts[2]['zoomFactor'], value
                checks = {item['label']: item for item in value['viewportChecks']}
                normal = checks['normal-five']
                for label, columns in [('normal-five', 5), ('normal-1600', 4), ('normal-1280', 3), ('normal-1000', 2)]:
                    item = checks[label]
                    assert len(item['layout']['tracks']) == columns, item
                    assert item['zoomFactor'] == normal['zoomFactor'], item
                    assert item['viewHeight'] == normal['viewHeight'], item
                    assert (item['minimumWidth'], item['minimumHeight']) == (960, 540), item
                    for card, original in zip(item['layout']['cards'], normal['layout']['cards']):
                        assert card['id'] == original['id'] and all(abs(card[key] - original[key]) <= 1 for key in ('width', 'height')), item
                    grid, container = item['layout']['grid'], item['layout']['container']
                    assert abs((grid['left'] - container['left']) - (container['right'] - grid['right'])) <= 1, item
                flat = checks['flat']
                assert flat['viewHeight'] > flat['clientHeight'], flat
                assert flat['layout'] == checks['normal-1280']['layout'], flat
                assert checks['flat-scrolled']['scrollY'] > 0, checks['flat-scrolled']
                portrait = checks['portrait']
                assert len(portrait['layout']['tracks']) == 2, portrait
                minimum = checks['portrait-minimum']
                assert (minimum['minimumWidth'], minimum['minimumHeight']) == (540, 960), minimum
                assert minimum['viewWidth'] > minimum['clientWidth'] and minimum['viewHeight'] > minimum['clientHeight'], minimum
                assert len(minimum['layout']['tracks']) == 2, minimum
                assert all(card['width'] == portrait['layout']['cards'][i]['width'] for i, card in enumerate(minimum['layout']['cards'])), minimum
                settings, panned = checks['clipped-settings'], checks['clipped-settings-scrolled']
                assert settings['layout']['dialog'] and panned['scrollX'] > 0 and panned['scrollY'] > 0, panned
                assert settings['layout']['dialog'] == panned['layout']['dialog'], panned
                if options.background_stability:
                    frames = value['settingsBackground']['frames']
                    before = value['settingsBackground']['before']
                    shifted = [frame for frame in frames if frame['width'] != before['width'] or frame['scroll'] != before['scroll'] or frame['cards'] != before['cards']]
                    value['settingsBackground']['shiftedFrames'] = len(shifted)
                    if options.expect_background_shift: assert shifted, 'Expected background layout bug to reproduce'
                    else: assert not shifted, value['settingsBackground']
                    if not options.expect_background_shift: assert abs(value['settingsBackground']['beforeZoom'] - value['settingsBackground']['afterZoom']) < .0001, value
                output = ROOT / 'output'
                output.mkdir(exist_ok=True)
                for width in (1366, 1920, 2560):
                    (output / f'ezread-native-five-columns-{width}.png').write_bytes(Path(f'{report}.{width}.png').read_bytes())
                for label in checks:
                    (output / f'ezread-native-viewport-{label}.png').write_bytes(Path(f'{report}.{label}.png').read_bytes())
                assert value['page']['stored'] == 'year' and value['page']['draftPresent'], value
                assert not value['page']['errors'], value['page']
                assert (data / 'desktop-window.json').exists(), 'Close handshake did not save native window state.'
                reports.append(value)
                marker.write_text('{"ezread-sort":"recent"}', encoding='utf-8')
            (ROOT / 'work' / 'webview2-native-smoke.json').write_text(json.dumps({'cycles': reports, 'duplicateLaunch': 'passed', 'realModelCalls': 0, 'realLibraryWrites': 0, 'nativeTaskbarPhotographed': False}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'cycles': len(reports), 'nativeWindow': True, 'engine': reports[0]['webViewVersion'], 'bookIconMatches': True, 'draftAndSortPersistAfterReopen': True, 'duplicateLaunch': 'passed', 'fixedSizeColumns': [5, 4, 3, 2], 'portraitColumns': 2, 'verticalCropAndModalPanning': True, 'minimumWindowArea': 'one quarter of monitor working area', 'uniformScaleAtWidths': [1366, 1920, 2560], 'realModelCalls': 0}))
            # WebView2's browser child can briefly retain profile file handles.
            time.sleep(1)
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__': main()
