"""Run the same source and offline checks locally and in CI."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(command, *, env=None):
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--lint-only', action='store_true',
        help='Check source quality without running Python and Node regressions.',
    )
    args = parser.parse_args()
    if importlib.util.find_spec('ruff') is None:
        raise SystemExit(
            'Ruff is required for source checks. '
            'Install the development dependencies: python -m pip install -r requirements-dev.txt'
        )
    run([sys.executable, '-m', 'ruff', 'check', '--no-cache', '.'])
    run([sys.executable, 'scripts/audit-source.py'])
    if args.lint_only:
        print('All source checks passed.')
        return

    # Existing tests create their own temporary databases below this ignored path.
    (ROOT / 'work').mkdir(exist_ok=True)
    node = os.environ.get('EZREAD_NODE') or shutil.which('node')
    if not node or not Path(node).is_file():
        raise SystemExit('Node.js is required for offline checks. Install it or set EZREAD_NODE to its executable path.')
    # Keep subprocess temp files inside the checkout, including on restricted hosts.
    # The environment change applies only to this check run, never the user's shell.
    with tempfile.TemporaryDirectory(prefix='offline-checks-', dir=ROOT / 'work') as temp:
        test_env = dict(os.environ, TEMP=temp, TMP=temp, TMPDIR=temp)
        run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], env=test_env)
        for path in sorted((ROOT / 'static').rglob('*.js')):
            run([node, '--check', str(path)], env=test_env)
        for path in sorted((ROOT / 'tests').glob('test_*.js')):
            run([node, str(path)], env=test_env)
    print('All source and offline checks passed. No real model inference was performed.')


if __name__ == '__main__':
    main()
