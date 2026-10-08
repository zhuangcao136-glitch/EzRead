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
    varied_cards = False
    def log_message(self, *args): pass

    def do_GET(self):
        name = self.path.split('?', 1)[0]
        covers = {'square': (320, 320), 'tall': (200, 600), 'wide': (600, 160)}
        if name.startswith('/fixture-cover/') and name.rsplit('/', 1)[-1].removesuffix('.svg') in covers:
            width, height = covers[name.rsplit('/', 1)[-1].removesuffix('.svg')]
            content = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="white"/><rect x="20" y="20" width="{width - 40}" height="{height - 40}" rx="20" fill="#d0e6df" stroke="#537b6b" stroke-width="4"/></svg>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers(); self.wfile.write(content)
            return
        if name.startswith('/api/'):
            paper = {'id': 'smoke-paper', 'title': 'WebView2 verification paper', 'authors': 'Test Author', 'journal': 'Test Journal', 'year': 2026, 'paper_type': 'journal', 'figures': [], 'blocks': [], 'pages': [], 'translation': {'status': 'idle'}}
            papers = [dict(paper, id='smoke-paper' if i == 0 else f'smoke-paper-{i}', title=f'Uniform scaling verification paper {i + 1}: methods and results') for i in range(self.paper_count)]
            if self.varied_cards:
                titles = ['Soft robotic sensing',
                          'Contact-aware manipulation using distributed optical tactile sensing and morphology-adaptive robotic fingertips',
                          'Tactile-based exploration, mapping and navigation with collision-resilient aerial vehicles',
                          'Learning proprioceptive representations for flexible grippers across complex contact conditions and environments']
                for i, item in enumerate(papers):
                    tier = ['top', 'important', 'other', 'conference', 'preprint'][i % 5]
                    item.update(title=f'{titles[i % len(titles)]} {i + 1}', journal_tier=tier,
                                paper_type=tier if tier in ('conference', 'preprint') else 'journal', conference_abbr='CASE',
                                paper_ai={'model': 'gpt-6.1-sol', 'reasoning_effort': 'low'})
                    if i % 4:
                        item['cover_url'] = f"/fixture-cover/{['square', 'tall', 'wide'][i % 3]}.svg"
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
    parser.add_argument('--varied-cards', action='store_true', help='Include long titles, all card tiers and square/tall/wide synthetic covers.')
    options = parser.parse_args()
    executable = options.executable.resolve()
    Handler.paper_count = 40 if options.background_stability else 5
    Handler.varied_cards = options.varied_cards
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
                (ROOT / 'work' / 'webview2-native-latest-cycle.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                if options.background_stability:
                    (ROOT / 'work' / f'settings-background-native-{"before" if options.expect_background_shift else "verification"}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                assert value['bigIconMatchesBook'] and value['captionIconOwned'], value
                assert value['windowTitle'] == '\u200b', value
                assert value['page']['desktop'] and value['page']['body'] and value['page']['closeHook'], value
                assert value['page']['cards'] == Handler.paper_count, value
                layouts = value['layouts']
                assert len(layouts) == 3, value
                if options.varied_cards:
                    expected_covers = sum(i % 4 != 0 for i in range(Handler.paper_count))
                    for item in layouts:
                        assert item['cardBox']['coverCount'] == item['cardBox']['loadedCovers'] == expected_covers, item['cardBox']
                        assert len(item['cardBox']['coverRatios']) == 3, item['cardBox']
                baseline = layouts[0]['layout']
                for item in layouts:
                    layout = item['layout']
                    assert abs(layout['viewport'] - 1920) <= 1, item
                    assert len(layout['tracks']) == 5, item
                    assert len({card['x'] for card in layout['cards']}) == 5, item
                    assert abs(layout['sidebar'] - baseline['sidebar']) <= 1, item
                    for card, original in zip(layout['cards'], baseline['cards']):
                        assert card['id'] == original['id'] and card['title'] == original['title'], item
                        for key in ('x', 'y', 'width', 'height'):
                            assert abs(card[key] - original[key]) <= 1, f"Monitor {item['label']}, card {card['id']}, {key}: {card[key]} versus {original[key]}"
                assert layouts[0]['zoomFactor'] < layouts[1]['zoomFactor'] < layouts[2]['zoomFactor'], value
                checks = {item['label']: item for item in value['viewportChecks']}
                dpi_checks = value['dpiChecks']
                assert dpi_checks, 'Actual monitor DPI coverage is missing.'
                for item in dpi_checks:
                    if item['label'].endswith('-full') and item['monitorWidth'] >= item['monitorHeight']:
                        assert len(item['layout']['tracks']) == 5, f"Full-width DPI layout lost a column: {item['label']}"
                    if item['label'].endswith(('-full', '-half')):
                        assert not item['verticalScroll'], f"Full-height DPI layout gained vertical scrolling: {item['label']}"
                    if item['label'].endswith('-half'):
                        assert len(item['layout']['tracks']) == 2, item
                for item in (*layouts, *checks.values(), *dpi_checks):
                    assert not item['horizontalScroll'], f"Unexpected horizontal scrollbar: {item['label']}"
                    assert not item['verticalScroll'], f"Unexpected outer vertical scrollbar: {item['label']}"
                    assert item['viewWidth'] <= item['clientWidth'], item
                    assert item['layout']['documentWidth'] <= item['layout']['innerWidth'], f"Page horizontal overflow: {item['label']}"
                    grid, container = item['layout']['grid'], item['layout']['container']
                    assert grid['left'] >= container['left'] and grid['right'] <= container['right'] + 1, item
                for monitor in ('1366x768', '1920x1080', '2560x1440', '1280x1440', '1080x1920'):
                    prefix = f'snap-{monitor}'
                    full, half, restored = (checks[f'{prefix}-{stage}'] for stage in ('full', 'half', 'restored'))
                    for item in (full, half, restored):
                        assert not item['verticalScroll'], f"Unnecessary full-height vertical scrollbar: {item['label']}"
                        assert item['viewHeight'] <= item['clientHeight'], item
                        assert item['scrollY'] == 0, item
                        assert item['zoomFactor'] == full['zoomFactor'], item
                        footer = item['layout']['footer']
                        assert abs(footer['bottom'] - item['layout']['height']) <= 1, item
                    assert len(half['layout']['tracks']) == 2, half
                    assert restored['layout'] == full['layout'], restored
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
                wheels = {item['label']: item for item in value['wheelChecks']}
                before, sidebar, cards = (wheels[label] for label in ('before', 'sidebar-down', 'cards-down'))
                assert sidebar['offset'] > before['offset'] and sidebar['viewTop'] == -sidebar['offset'], sidebar
                assert sidebar['page']['cardScroll'] == before['page']['cardScroll'], sidebar
                assert sidebar['page']['sidebarScroll'] == before['page']['sidebarScroll'], sidebar
                assert cards['offset'] == sidebar['offset'] and cards['page']['cardScroll'] > sidebar['page']['cardScroll'], cards
                assert wheels['sidebar-up']['offset'] < cards['offset'], wheels
                assert wheels['sidebar-bottom']['offset'] == wheels['sidebar-bottom']['maximum'], wheels
                assert wheels['sidebar-bottom-again']['offset'] == wheels['sidebar-bottom']['offset'], wheels
                assert wheels['sidebar-top']['offset'] == 0, wheels
                assert wheels['modal-restored']['offset'] == wheels['modal-restored']['before'], wheels
                for label, item in wheels.items():
                    if label.endswith('-wheel-down'):
                        assert item['offset'] > 0 and item['viewTop'] == -item['offset'], item
                        baseline = wheels[label.removesuffix('-wheel-down') + '-wheel-before']
                        assert item['page']['cardScroll'] == baseline['page']['cardScroll'], item
                        up = wheels[label.removesuffix('-wheel-down') + '-wheel-up']
                        assert up['offset'] == 0 and up['page']['cardScroll'] == baseline['page']['cardScroll'], up
                portrait = checks['portrait']
                assert len(portrait['layout']['tracks']) == 2, portrait
                minimum = checks['portrait-minimum']
                assert minimum['minimumWidth'] >= 540 and minimum['minimumHeight'] == 960, minimum
                assert minimum['viewWidth'] <= minimum['clientWidth'] and minimum['viewHeight'] > minimum['clientHeight'], minimum
                assert len(minimum['layout']['tracks']) == 2, minimum
                assert all(card['width'] == portrait['layout']['cards'][i]['width'] for i, card in enumerate(minimum['layout']['cards'])), minimum
                settings, scrolled = checks['short-settings'], checks['short-settings-wheel']
                for item in (settings, scrolled):
                    assert item['layout']['dialog'] and item['scrollY'] == 0, item
                    assert item['viewHeight'] <= item['clientHeight'], item
                    for dialog in item['layout']['dialog']:
                        assert dialog['top'] >= 0 and dialog['bottom'] <= item['layout']['height'] + 1, item
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
                for item in dpi_checks:
                    (output / f"ezread-native-{item['label']}.png").write_bytes(Path(f"{report}.{item['label']}.png").read_bytes())
                assert value['page']['stored'] == 'year' and value['page']['draftPresent'], value
                assert not value['page']['errors'], value['page']
                assert (data / 'desktop-window.json').exists(), 'Close handshake did not save native window state.'
                reports.append(value)
                marker.write_text('{"ezread-sort":"recent"}', encoding='utf-8')
            (ROOT / 'work' / 'webview2-native-smoke.json').write_text(json.dumps({'cycles': reports, 'duplicateLaunch': 'passed', 'realModelCalls': 0, 'realLibraryWrites': 0, 'nativeTaskbarPhotographed': False}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'cycles': len(reports), 'nativeWindow': True, 'engine': reports[0]['webViewVersion'], 'bookIconMatches': True, 'draftAndSortPersistAfterReopen': True, 'duplicateLaunch': 'passed', 'fixedSizeColumns': [5, 4, 3, 2], 'portraitColumns': 2, 'sidebarWheelPansPage': True, 'outerVerticalScrollbar': False, 'dialogsFitVisibleHeight': True, 'minimumWindowWidth': 'at least two columns or half monitor width', 'minimumWindowHeight': 'half monitor height', 'horizontalScrollbars': False, 'uniformScaleAtWidths': [1366, 1920, 2560], 'variedCards': options.varied_cards, 'realModelCalls': 0}))
            # WebView2's browser child can briefly retain profile file handles.
            time.sleep(1)
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__': main()
