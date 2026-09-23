#!/usr/bin/env bash
# Full release gate. It uses synthetic profiles only and never touches production.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$PWD
image=${CHECK_IMAGE:-fitbaus-fitbaus:check}
results=${TEST_OUTPUT_DIR:-$root/test-results}
mkdir -p "$results"
results=$(cd "$results" && pwd)
runtime=$(mktemp -d)
container="fitbaus-check-$$"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; rm -rf "$runtime"; }
trap cleanup EXIT
python3 scripts/audit.py
python3 scripts/docs.py --check
git diff --check
npm test
for file in app.js js/*.js; do node --check "$file"; done
docker build -t "$image" . > "$results/build.log" 2>&1
run=(docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp --user 10001:10001 -e PYTHONDONTWRITEBYTECODE=1 -e FITBAUS_AUTO_SYNC_ENABLED=false -e FITFLARE_PROFILE_ID=Demo)
"${run[@]}" --entrypoint python "$image" scripts/audit.py --image
"${run[@]}" -v "$root/tests:/app/tests:ro" --entrypoint python "$image" -m unittest discover -s tests -p 'test_*.py' -v 2>&1 | tee "$results/backend-tests.log"
python3 tests/fixture.py "$runtime/profiles"
# These are generated public fixtures, not production health/authorization files.
chmod -R a+rX "$runtime"
password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
export PREVIEW_ADMIN_PASSWORD="$password" TEST_PROFILE=Demo SYNTHETIC_FIXTURE=1
start_preview() {
  docker run -d --name "$container" --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp --user 10001:10001 \
    -p 127.0.0.1::9000 -v "$runtime/profiles:/app/profiles:ro" \
    -e PYTHONDONTWRITEBYTECODE=1 -e FITBAUS_AUTO_SYNC_ENABLED=false \
    -e FITBAUS_ADMIN_PASSWORD="$password" -e FITBAUS_SESSION_COOKIE_SECURE=false \
    -e FITFLARE_PROFILE_ID=Demo -e FITBAUS_DATA_ACCESS="$1" -e FITBAUS_PUBLIC_PROFILES=Demo "$image" >/dev/null
  port=$(docker port "$container" 9000/tcp | sed 's/.*://')
  export BASE_URL="http://127.0.0.1:$port"
  for attempt in $(seq 1 40); do
    if curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1; then return; fi
    sleep 1
  done
  docker logs --tail 30 "$container"
  return 1
}
start_preview public
TEST_OUTPUT_DIR="$results" npm run test:browser
DOCS_ACCESS_MODE=public TEST_OUTPUT_DIR="$results" node tests/docs-browser.cjs
# Synthetic preview cannot contact Fitbit: there are no authorized tokens, and auto-sync is off.
docker rm -f "$container" >/dev/null
start_preview private
node tests/private-browser.cjs
DOCS_ACCESS_MODE=private TEST_OUTPUT_DIR="$results" node tests/docs-browser.cjs
printf '%s\n' "$(docker image inspect "$image" --format '{{.Id}}')" > "$results/validated-image.txt"
node -e 'require("fs").writeFileSync(process.argv[1], JSON.stringify({status:"passed", completed_at:new Date().toISOString(), fixture:"synthetic"},null,2))' "$results/release-gate.json"
printf 'Release checks passed. Validated image: %s\n' "$image"
