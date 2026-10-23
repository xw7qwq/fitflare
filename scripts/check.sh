#!/usr/bin/env bash
# Full release gate. It uses synthetic profiles only and never touches production.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$PWD
image=${CHECK_IMAGE:-fitflare:check}
results=${TEST_OUTPUT_DIR:-$root/test-results}
mkdir -p "$results"
results=$(cd "$results" && pwd)
# A failed run must not leave a previous success receipt behind.
rm -f "$results/release-gate.json" "$results/validated-image.txt"
runtime=$(mktemp -d)
container="fitflare-check-$$"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; rm -rf "$runtime"; }
trap cleanup EXIT
python3 scripts/audit.py
python3 scripts/audit_history.py
python3 -m unittest tests.test_history_audit -v
python3 scripts/docs.py --check
git diff --check
npm test
for file in app.js js/*.js; do node --check "$file"; done
revision=$(git rev-parse HEAD)
source_dirty=false
[[ -z "$(git status --porcelain)" ]] || source_dirty=true
docker build -f "${CHECK_DOCKERFILE:-Dockerfile}" \
  --label "org.opencontainers.image.source=https://github.com/xw7qwq/fitflare" \
  --label "org.opencontainers.image.revision=$revision" \
  --label "dev.fitflare.source-dirty=$source_dirty" \
  -t "$image" . > "$results/build.log" 2>&1
run=(docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp --user 10001:10001 -e PYTHONDONTWRITEBYTECODE=1 -e FITFLARE_AUTO_SYNC_ENABLED=false -e FITFLARE_PROFILE_ID=Demo -e FITFLARE_DATA_ACCESS=public)
"${run[@]}" --entrypoint python "$image" scripts/audit.py --image
"${run[@]}" -v "$root/tests:/app/tests:ro" --entrypoint python "$image" -m unittest discover -s tests -p 'test_*.py' -v 2>&1 | tee "$results/backend-tests.log"
python3 tests/fixture.py "$runtime/profiles"
# These are generated public fixtures, not production health/authorization files.
chmod -R a+rX "$runtime"
export TEST_PROFILE=Demo SYNTHETIC_FIXTURE=1
start_preview() {
  docker run -d --name "$container" --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp --user 10001:10001 \
    -p 127.0.0.1::9000 -v "$runtime/profiles:/app/profiles:ro" \
    -e PYTHONDONTWRITEBYTECODE=1 -e FITFLARE_AUTO_SYNC_ENABLED=false \
    -e FITFLARE_PROFILE_ID=Demo -e FITFLARE_DATA_ACCESS=public "$image" >/dev/null
  port=$(docker port "$container" 9000/tcp | sed 's/.*://')
  export BASE_URL="http://127.0.0.1:$port"
  for attempt in $(seq 1 40); do
    if curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1; then return; fi
    sleep 1
  done
  docker logs --tail 30 "$container"
  return 1
}
start_preview
TEST_OUTPUT_DIR="$results" npm run test:browser
TEST_OUTPUT_DIR="$results" node tests/docs-browser.cjs
validated=$(docker image inspect "$image" --format '{{.Id}}')
printf '%s\n' "$validated" > "$results/validated-image.txt"
node -e 'require("fs").writeFileSync(process.argv[1], JSON.stringify({status:"passed", completed_at:new Date().toISOString(), fixture:"synthetic", revision:process.argv[2], source_dirty:process.argv[3]==="true", image:process.argv[4], image_id:process.argv[5]},null,2)+"\n")' "$results/release-gate.json" "$revision" "$source_dirty" "$image" "$validated"
printf 'Release checks passed. Validated image: %s\n' "$image"
