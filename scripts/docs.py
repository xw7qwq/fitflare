#!/usr/bin/env python3
"""Regenerate the checked-in Markdown reference, or reject documentation drift."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.api_docs import render_markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    target = ROOT / 'API.md'
    expected = render_markdown()
    if args.check:
        if not target.is_file() or target.read_text(encoding='utf-8') != expected:
            raise SystemExit('API.md differs from its catalog; run python3 scripts/docs.py')
        print('Documentation source parity passed')
    else:
        target.write_text(expected, encoding='utf-8')
        print('Generated API.md from common/api_docs.py')


if __name__ == '__main__':
    main()
