#!/usr/bin/env bash
# Check -> validate runtime -> deploy -> verify -> automatic image rollback on failure.
set -euo pipefail
cd "$(dirname "$0")/.."
exec 9>".git/deploy.lock"
flock -n 9 || { echo 'Another deployment is in progress.'; exit 1; }
image="fitbaus-fitbaus:release-$(date -u +%Y%m%dT%H%M%SZ)"
CHECK_IMAGE="$image" bash scripts/check.sh
validated=$(cat "${TEST_OUTPUT_DIR:-test-results}/validated-image.txt")
[[ "$validated" == "$(docker image inspect "$image" --format '{{.Id}}')" ]]
# Refuse to interrupt a sync that currently holds a profile lock.
docker exec -i fitbaus-app python - <<'PY'
import fcntl
from pathlib import Path
for path in Path('/app/profiles').glob('*/cache/.fetch.lock'):
    with path.open('r+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Active Fitbit sync; retry deployment after it completes.')
PY
runtime=$(docker compose config --format json)
printf '%s' "$runtime" | python3 -c 'import json,sys; c=json.load(sys.stdin)["services"]["fitbaus"]; assert c["user"].split(":")[0] not in ("0","root"), "Configure a non-root FITBAUS_UID first"; assert c.get("read_only")'
unset runtime
old=$(docker inspect fitbaus-app --format '{{.Image}}')
docker tag "$old" fitbaus-fitbaus:rollback
rollback() {
  echo 'Deployment validation failed; restoring the preceding image.' >&2
  FITBAUS_IMAGE="$old" docker compose up -d --no-deps --no-build fitbaus
}
trap rollback ERR
FITBAUS_IMAGE="$image" docker compose up -d --no-deps --no-build fitbaus
for attempt in $(seq 1 45); do
  health=$(docker inspect fitbaus-app --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}')
  [[ "$health" == healthy ]] && break
  sleep 1
done
[[ "$health" == healthy ]]
docker exec -i fitbaus-app python - <<'PY'
import json
import urllib.request
from urllib.error import HTTPError
base = 'http://127.0.0.1:9000'
def get(path):
    with urllib.request.urlopen(base+path, timeout=10) as response:
        return json.load(response)
session = get('/api/admin/session')
if session['data_access'] == 'public':
    profiles = get('/api/profiles')
    if profiles:
        from urllib.parse import quote
        data = get('/api/dashboard/' + quote(profiles[0]['name']) + '?tables=none')
        assert 'files' not in data and 'tables' not in data
else:
    try:
        get('/api/profiles')
    except HTTPError as error:
        assert error.code == 401
    else:
        raise SystemExit('Private access boundary failed')
print('Production data access and response checks passed.')
PY
# Keep the existing compose default pointing to the successfully validated build.
docker tag "$image" fitbaus-fitbaus:latest
trap - ERR
printf 'Deployed %s; rollback image preserved at fitbaus-fitbaus:rollback\n' "$image"
