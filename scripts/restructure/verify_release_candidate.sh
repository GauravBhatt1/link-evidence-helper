#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'release-candidate gate failed: %s\n' "$1" >&2
  exit 1
}

required_paths=(
  apps/api
  apps/worker
  apps/browser-worker
  apps/web
  packages/contracts
  deploy/compose.yml
  deploy/caddy/Caddyfile.preview
  docs/restructure/PROJECT_STATE.md
)

for path in "${required_paths[@]}"; do
  [[ -e "$path" ]] || fail "missing required path: $path"
done

if grep -RIn --exclude-dir=.git --exclude='verify_release_candidate.sh' \
  -E '(^|[^0-9])8765([^0-9]|$)' deploy .github/workflows docs/restructure | \
  grep -v 'PROJECT_STATE.md'; then
  fail 'protected production port 8765 appears in non-production release-candidate assets'
fi

if grep -RIn --exclude-dir=.git -E '(password|token|secret|cookie)[[:space:]]*[:=][[:space:]]*[^$<{[:space:]]' \
  deploy .github/workflows 2>/dev/null; then
  fail 'possible literal credential found in deployment or workflow files'
fi

gofmt_output="$(gofmt -l apps packages 2>/dev/null || true)"
[[ -z "$gofmt_output" ]] || fail "Go formatting required: $gofmt_output"

go test -race ./...
go vet ./...

npm ci
npm run --workspace @link-evidence/contracts typecheck
npm run --workspace @link-evidence/web typecheck
npm run --workspace @link-evidence/web test
npm run --workspace @link-evidence/browser-worker typecheck
npm run --workspace @link-evidence/browser-worker test

printf 'release-candidate repository gates passed\n'
