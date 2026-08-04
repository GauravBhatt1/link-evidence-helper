# Milestone 4 development Go Search API

Milestone 4 adds a development-only Go HTTP boundary and a typed React client.
It does not contact live sources or replace the Python production service.

## Safety boundary

- Go binds to `127.0.0.1:8780` by default.
- Vite binds to `127.0.0.1:5173`.
- The API reads only sanitized files from `packages/testing/fixtures`.
- Fixture mode remains the React default.
- API mode requires `VITE_SEARCH_TRANSPORT=api`.
- No CORS, credentials, cookies, tokens, TMDB, Jellyfin, Redis, PostgreSQL,
  Python API proxy, resolver, or Delivery Links are enabled.
- Port `8765`, production Docker, Compose, volumes, SQLite, and adapters are not
  modified.

## API routes

```text
GET /healthz
GET /api/v1/search?q=<title>
```

Search accepts exactly one `q` parameter. Query normalization is limited to
trimming, collapsing repeated whitespace, and case-insensitive exact fixture
alias matching. Unknown titles return the canonical empty response.

## Run the Go API

From the repository root:

```bash
cd apps/api
LINK_EVIDENCE_SEARCH_MODE=fixture \
LINK_EVIDENCE_API_ADDR=127.0.0.1:8780 \
LINK_EVIDENCE_FIXTURE_DIR=../../packages/testing/fixtures \
go run ./cmd/server
```

The server refuses a non-loopback address unless
`LINK_EVIDENCE_ALLOW_PUBLIC_LISTEN=true` is set explicitly. Public listening is
not required or approved for this development milestone.

## Run React against the Go API

In a second terminal from the repository root:

```bash
VITE_SEARCH_TRANSPORT=api \
corepack pnpm@10.18.3 --filter @link-evidence/web dev
```

Vite proxies same-origin `/api` requests only to
`http://127.0.0.1:8780`. The browser never receives or uses the API target
address directly.

## Tests

```bash
(cd apps/api && go test ./...)
corepack pnpm@10.18.3 --filter @link-evidence/web typecheck
corepack pnpm@10.18.3 --filter @link-evidence/web test
corepack pnpm@10.18.3 --filter @link-evidence/web test:browser
```

GitHub Actions additionally builds the Go server, starts it on loopback, runs a
React-to-Go Playwright integration test, stops the exact process, and verifies
that port `8780` is closed afterward.

## Deferred work

The `search.Searcher` interface is the compatibility seam for later live search
backends. HTTP-first source search, ranked failover, Redis coalescing, browser
workers, real resolution, and production routing remain deferred.
