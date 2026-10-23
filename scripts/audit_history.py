"""Block the known unsanitized credential history without reading or printing it."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Object identity only; the historical credential must never be copied here.
BLOCKED_OBJECTS = frozenset({'92b8ef9e36da7a926ac86d5fa9aa6d7bd0eba59f'})


def audit_history(root=ROOT, blocked_objects=BLOCKED_OBJECTS):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

    if git('rev-parse', '--is-shallow-repository') != 'false':
        raise ValueError('History audit requires a complete clone; fetch with --unshallow first.')
    objects = {line.split(' ', 1)[0] for line in git('rev-list', '--objects', '--all').splitlines()}
    if objects & blocked_objects:
        raise ValueError('Unsanitized legacy history is reachable. Do not push these branches or tags.')
    return len(objects)


if __name__ == '__main__':
    try:
        count = audit_history()
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(f'History audit passed ({count} reachable objects)')
