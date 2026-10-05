"""Run the same offline checks locally and in CI, without model requests."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command):
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    # Existing tests create their own temporary databases below this ignored path.
    (ROOT / 'work').mkdir(exist_ok=True)
    node = os.environ.get('EZREAD_NODE') or shutil.which('node')
    if not node or not Path(node).is_file():
        raise SystemExit('Node.js is required for offline checks. Install it or set EZREAD_NODE to its executable path.')
    run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    for path in sorted((ROOT / 'static').rglob('*.js')):
        run([node, '--check', str(path)])
    for name in ('test_reader.js', 'test_detail_notes.js', 'test_library_cards.js'):
        run([node, str(ROOT / 'tests' / name)])
    print('All offline checks passed. No real model inference was performed.')


if __name__ == '__main__':
    main()
