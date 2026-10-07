"""Start the real entry point with a temporary library and no model requests."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def test_fresh_start_serves_all_modules_and_persists_settings(self):
        (ROOT / 'work').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='startup-', dir=ROOT / 'work') as temp:
            data = Path(temp) / 'data'
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            env = {**os.environ, 'EZREAD_DATA_DIR': str(data)}
            with open(Path(temp) / 'server.log', 'w', encoding='utf-8') as log:
                process = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--port', str(port)],
                    cwd=ROOT, env=env, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                worker_pid = process.pid
                try:
                    base = f'http://127.0.0.1:{port}'
                    deadline = time.monotonic() + 10
                    health = None
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            self.fail('Entry point exited before becoming healthy')
                        try:
                            with urllib.request.urlopen(base + '/api/health', timeout=.5) as response:
                                health = json.load(response)
                            break
                        except (OSError, urllib.error.URLError):
                            time.sleep(.05)
                    self.assertIsNotNone(health)
                    self.assertEqual(Path(health['app_root']).resolve(), ROOT.resolve())
                    self.assertEqual(Path(health['data_dir']).resolve(), data.resolve())
                    if os.name == 'nt' and sys.prefix != sys.base_prefix:
                        # The Windows venv redirector has its own PID. Only use the
                        # worker PID after validating this test's exact root/library.
                        self.assertIsInstance(health['pid'], int)
                        self.assertGreater(health['pid'], 0)
                        worker_pid = health['pid']
                    else:
                        self.assertEqual(health['pid'], process.pid)
                    with urllib.request.urlopen(base, timeout=2) as response:
                        index = response.read().decode('utf-8')
                    scripts = re.findall(r'<script src="([^"]+)" defer></script>', index)
                    self.assertEqual(scripts[-1], '/static/app.js')
                    self.assertIn('/static/core/state.js', scripts)
                    self.assertIn('/static/core/storage.js', scripts)
                    self.assertIn('/static/library/cards.js', scripts)
                    for script in scripts:
                        with urllib.request.urlopen(base + script, timeout=2) as response:
                            self.assertIn('javascript', response.headers['Content-Type'])
                            self.assertTrue(response.read(), script)
                    request = urllib.request.Request(base + '/api/settings', method='PATCH',
                        data=b'{"ui_font_size":19}', headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        self.assertEqual(json.load(response)['ui_font_size'], 19)
                    with urllib.request.urlopen(base + '/api/settings', timeout=2) as response:
                        self.assertEqual(json.load(response)['ui_font_size'], 19)
                    with urllib.request.urlopen(base + '/api/papers', timeout=2) as response:
                        self.assertEqual(json.load(response)['papers'], [])
                finally:
                    # Only the child process created by this test is stopped.
                    if worker_pid != process.pid:
                        subprocess.run(['taskkill', '/PID', str(worker_pid), '/T', '/F'],
                                       capture_output=True, check=False)
                    process.terminate()
                    process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
