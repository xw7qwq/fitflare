#!/usr/bin/env bash
# Check -> validate runtime -> deploy -> verify -> automatic image rollback on failure.
set -euo pipefail
cd "$(dirname "$0")/.."
exec 9>".git/deploy.lock"
flock -n 9 || { echo 'Another deployment is in progress.'; exit 1; }
image="fitflare:release-$(date -u +%Y%m%dT%H%M%SZ)"
# A host may build on a locally patched base; the gate then validates that exact image.
if [[ -z "${CHECK_DOCKERFILE:-}" && -f Dockerfile.host ]]; then export CHECK_DOCKERFILE=Dockerfile.host; fi
CHECK_IMAGE="$image" bash scripts/check.sh
validated=$(cat "${TEST_OUTPUT_DIR:-test-results}/validated-image.txt")
[[ "$validated" == "$(docker image inspect "$image" --format '{{.Id}}')" ]]
# Refuse to interrupt a sync that currently holds a profile lock.
docker exec -i fitflare-app python - <<'PY'
import fcntl
from pathlib import Path
for path in Path('/app/profiles').glob('*/cache/.fetch.lock'):
    with path.open('r+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Active health data sync; retry deployment after it completes.')
PY
runtime=$(docker compose config --format json)
printf '%s' "$runtime" | python3 -c 'import json,sys; c=json.load(sys.stdin)["services"]["fitflare"]; assert c["user"].split(":")[0] not in ("0","root"), "Configure a non-root FITFLARE_UID first"; assert c.get("read_only")'
unset runtime
old=$(docker inspect fitflare-app --format '{{.Image}}')
docker tag "$old" fitflare:rollback
rollback() {
  echo 'Deployment validation failed; restoring the preceding image.' >&2
  FITFLARE_IMAGE="$old" docker compose up -d --no-deps --no-build fitflare
}
trap rollback ERR
FITFLARE_IMAGE="$image" docker compose up -d --no-deps --no-build fitflare
for attempt in $(seq 1 45); do
  health=$(docker inspect fitflare-app --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}')
  [[ "$health" == healthy ]] && break
  sleep 1
done
[[ "$health" == healthy ]]
docker exec -i fitflare-app python - <<'PY'
import json
import urllib.request
from urllib.error import HTTPError
base = 'http://127.0.0.1:9000'
def get(path):
    with urllib.request.urlopen(base+path, timeout=10) as response:
        return json.load(response)
session = get('/api/admin/session')
if session['data_access'] == 'public':
    # The compatibility list exposes at most the owner and honors the public allowlist.
    if get('/api/profiles'):
        data = get('/api/dashboard?tables=none')
        assert 'files' not in data and 'tables' not in data
else:
    try:
        get('/api/account')
    except HTTPError as error:
        assert error.code == 401
    else:
        raise SystemExit('Private access boundary failed')
print('Production data access and response checks passed.')
PY
# Keep the existing compose default pointing to the successfully validated build.
docker tag "$image" fitflare:latest
trap - ERR
printf 'Deployed %s; rollback image preserved at fitflare:rollback\n' "$image"
