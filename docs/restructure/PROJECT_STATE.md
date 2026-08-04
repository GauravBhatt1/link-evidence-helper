# Restructuring project state

Last updated: 2026-08-05

## Goal

Deliver one production-ready repository and deployment path for the existing link-evidence-helper application while keeping the current Python production service available as the behavioral oracle until final parity and rollback verification are complete.

## Target architecture

- React + TypeScript web application for presentation and user decisions
- Go API for contracts, validation, authentication boundaries, and job APIs
- Go workers for HTTP-first search, resolution, integrations, and orchestration
- TypeScript + Playwright browser workers only when JavaScript interaction is required
- Redis for ephemeral jobs, caching, request coalescing, and coordination
- PostgreSQL for durable application state
- Caddy for final routing and TLS
- Existing Python application retained until the final verified traffic switch

## Permanent safety rules

- Never expose secrets, credentials, cookies, signed URLs, or production data in Git history.
- Never delete production volumes or use destructive Git reset commands for rollback.
- Keep live production traffic on the current Python application until full parity, health, migration, and rollback checks pass.
- Browser automation must remain isolated from the API process.
- Search is HTTP-first; browser execution is fallback-only.
- Ordinary users never select sources directly.
- One content item equals one top-level card; the active card is the workspace.
- A release and any required quality must be selected before Find Links is enabled.
- Do not present fixture, mock, or disconnected behavior as live production functionality.

## Completed checkpoints

### Milestone 0 — audit and migration design

Completed read-only architecture audit, migration matrix, rollback boundaries, risk analysis, and scaling plan.

### Milestone 1 — contracts and scaffold

- Branch: `restructure/milestone-1-scaffold`
- Commit: `67587d2184ca505c25a3cb02a5301765dbf769ac`
- Canonical JSON Schemas, Go/TypeScript/Python representations, deterministic fixtures, contract parity tests, and isolated development Redis/PostgreSQL scaffold

### Milestone 2 — isolated React shell

- Branch: `restructure/milestone-2-react-shell`
- Original commit: `19f949528282664c92adceecca3f9242f611468d`
- Responsive React shell, navigation, route placeholders, accessibility, component tests, and seven-viewport browser tests

### Milestone 3 — fixture-driven React search workflow

- Source branch: `restructure/milestone-3-react-search`
- Reviewed head: `3d5819a389b5932b048eb5ccab2dc8f5e7927b6f`
- Squash merged into the cumulative restructuring line as: `ef53c42a88504c483cf657bc2dbee421a6d5fa42`
- Deterministic fixture search, unified content cards, active-card workspace, native release/quality selection, validated local resolution intent, cancellation/stale-response protection, accessibility fixes, and green CI
- Final CI: Python 109 passed; contract, Go, TypeScript, Compose safety, frontend typecheck, Vitest 55 passed, Playwright 53 passed with 3 expected desktop-only skips

### Milestone 4 — development Go Search API and typed React client

- Source branch: `restructure/milestone-4-go-search-api`
- Reviewed head: `e0f2587fc3f2b4eb425420b53628ba198469390d`
- Squash merged into the cumulative restructuring line as: `ec6db3ca70548f36e2bb74b6e7e4f6645a6b3e81`
- Added loopback-only Go HTTP service with `GET /healthz` and `GET /api/v1/search?q=`
- Added exact deterministic sanitized-fixture backend, canonical empty/error responses, strict method/parameter boundaries, request IDs, and safe errors
- Added same-origin typed React API transport with canonical schema validation and explicit `VITE_SEARCH_TRANSPORT=api` opt-in
- Fixture mode remains the default; Vite proxies `/api` to `127.0.0.1:8780` only in explicit API mode
- Final CI: all contract jobs passed; Go API integration passed; frontend typecheck passed; Vitest 62 passed; fixture-mode Playwright 53 passed with 10 expected mode/viewport skips; dedicated React-to-Go browser integration passed; loopback API cleanup passed

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`ec6db3ca70548f36e2bb74b6e7e4f6645a6b3e81`

This branch is the cumulative non-production integration line for all remaining work. Documentation updates may appear after the recorded implementation commit.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, or mounted volumes; those checks must be executed on the VPS during release-candidate and final-cutover verification.

## Remaining work

1. Redis-backed jobs, caching, request coalescing, cancellation, bounded concurrency, and idempotency
2. Go HTTP-first live source search and ranked failover parity with the Python behavioral oracle
3. TypeScript Playwright browser-worker fallback
4. Link-resolution workflow and Delivery Links UI
5. Movies, TV, Missing, Recently Added, and Jellyfin integration
6. Admin authentication, source management, diagnostics, and audit-safe events
7. PostgreSQL durable schema and SQLite migration/rollback tooling
8. Production Docker Compose, health checks, Caddy routing, observability, backup, and restore
9. Full parity, load, security, migration, rollback, and release-candidate verification
10. Final controlled deployment and traffic switch

## Working method

- Continue through internal checkpoints without requiring the user to copy large milestone prompts.
- Use focused branches and pull requests into `restructure/integration`.
- Require green CI before each integration merge.
- Keep `master` and production untouched until a final release candidate is complete.
- Stop only for an actual external blocker, missing production-only information, or the final VPS deployment/cutover action.

## Next action

Create the next focused branch from `restructure/integration` and implement the Redis-backed development job foundation: canonical job creation/status APIs, idempotency, request coalescing, bounded concurrency, cancellation semantics, TTL cleanup, and tests. Do not connect live sources, production traffic, or the current Python service yet.
