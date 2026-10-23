#!/usr/bin/env python3
"""Send the health snapshot to the lucius7.dev panel now, or write it to a file to inspect."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.panel_push import PanelPushError, build_snapshot, encode, push
from common.profile_paths import owner_profile_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', metavar='FILE', help='write the snapshot JSON to FILE instead of sending it')
    args = parser.parse_args()
    profile_id = owner_profile_id()
    if args.output:
        snapshot = build_snapshot(profile_id)
        body = encode(snapshot)
        Path(args.output).write_bytes(body)
        print(f"Wrote {len(body)} bytes to {args.output}: {len(snapshot['metrics'])} metric cards, "
              f"{len(snapshot['daily']['points'])} days, {len(snapshot['weekly']['points'])} weeks, "
              f"{len(snapshot['sleep'])} sleep records")
        return
    try:
        result = push(profile_id)
    except PanelPushError as error:
        raise SystemExit(f'Panel push failed: {error}')
    state = 'stored' if result.get('stored') else 'unchanged, nothing written'
    print(f"Pushed {result.get('bytes')} bytes at {result.get('received_at')} ({state})")


if __name__ == '__main__':
    main()
