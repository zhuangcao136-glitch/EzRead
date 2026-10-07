"""Codex-friendly journal catalogue CLI. Same validation and storage as settings."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ezread.config import data_directory
from ezread import journal_catalogue as catalogue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=data_directory(ROOT))
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list')
    listing.add_argument('--tier', choices=catalogue.TIERS)
    listing.add_argument('--query', default='')
    applying = commands.add_parser('apply', help='Apply {revision, changes} from a UTF-8 JSON file')
    applying.add_argument('file', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'apply':
            value = json.loads(args.file.read_text(encoding='utf-8-sig'))
            result = catalogue.apply_changes(args.data_dir, value['changes'], value['revision'])
        else:
            result = catalogue.snapshot(args.data_dir)
            result['rows'] = [r for r in result['rows'] if (not args.tier or r['tier'] == args.tier)
                              and args.query.casefold() in ' '.join(r.values()).casefold()]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
