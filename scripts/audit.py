"""Reject sensitive tracked/build files without printing their contents."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def forbidden(path):
    parts = path.parts
    name = path.name.lower()
    return (
        ('profiles' in parts and name != '.gitkeep') or 'secrets' in parts
        or (name.startswith('.env') and not name.endswith('.example'))
        or name == 'client.json' or name.startswith('tokens') and '.json' in name
        or path.suffix.lower() in {'.pem', '.key', '.p12'}
        or 'test-results' in parts or 'playwright-report' in parts
    )


def main():
    image = '--image' in sys.argv
    if image:
        paths = [path.relative_to(ROOT) for path in ROOT.rglob('*') if path.is_file()]
        if (ROOT / '.git').exists():
            raise SystemExit('Image unexpectedly contains Git metadata')
    else:
        listing = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
        paths = [Path(value.decode()) for value in listing.split(b'\0') if value]
    problems = []
    for path in set(paths):
        if forbidden(path):
            problems.append(str(path))
            continue
        target = ROOT / path
        if not target.is_file() or target.suffix not in {'.py', '.js', '.json', '.yml', '.yaml', '.md', '.sh', '.txt'}:
            continue
        text = target.read_text(errors='replace')
        private_header = '-----BEGIN ' + '(?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
        if re.search(private_header, text):
            problems.append(str(path) + ' (private key material)')
    if problems:
        raise SystemExit('Sensitive files detected: ' + ', '.join(sorted(problems)))
    print(f"{'Image' if image else 'Source'} audit passed ({len(set(paths))} files)")


if __name__ == '__main__':
    main()
