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
- Squash merged as: `ef53c42a88504c483cf657bc2dbee421a6d5fa42`
- Deterministic fixture search, unified content cards, active-card workspace, native release/quality selection, validated local resolution intent, cancellation/stale-response protection, accessibility fixes, and green CI

### Milestone 4 — development Go Search API and typed React client

- Source branch: `restructure/milestone-4-go-search-api`
- Reviewed head: `e0f2587fc3f2b4eb425420b53628ba198469390d`
- Squash merged as: `ec6db3ca70548f36e2bb74b6e7e4f6645a6b3e81`
- Added loopback-only Go HTTP service, deterministic sanitized-fixture search, canonical safe errors, and explicit typed React API transport

### Milestone 5 — Redis-backed job foundation

- Source branch: `restructure/milestone-5-redis-jobs`
- Reviewed head: `4a31e5b1b898882dbd23cbdec49f1f4681673067`
- Squash merged as: `7f11f13e71e50ac0294230c370838632db4bd3c3`
- Added canonical Redis job/event storage, atomic idempotency and coalescing, subscriber cancellation, bounded queueing, recovery, cleanup, API routes, and a disconnected development executor

### Milestone 6 — hardened HTTP-first shadow search

- Source branch: `restructure/milestone-6-http-search-workers`
- Reviewed head: `a9c82c73912f7aa90ddf869ae14c83aa31e8294a`
- Pull request: `#5`
- Added concurrent ranked source search, isolated partial failure, strict versioned source configuration, bounded HTTP handling, DNS pinning, private-network rejection, redirect restrictions, proxy isolation, duplicate suppression, and Go aggregation/parity coverage
- No built-in live sources or production routing were added

### Milestone 7 — isolated Playwright browser fallback

- Source branch: `restructure/browser-worker-fallback`
- Reviewed head: `ce9a77636a5d04b8cb8d53ff66c650e52036e8a9`
- Pull request: `#6`
- Added a credential-free browser worker with strict task validation, DNS and network safety, host pinning, request/resource restrictions, bounded extraction, cancellation, safe errors, and deterministic local-browser tests
- Browser execution remains isolated and fallback-only

### Milestone 8 — verified resolution and Delivery Links

- Source branch: `restructure/resolution-delivery-workflow`
- Reviewed head: `dd508a9e29ab3e303ec78b8a969d275cf4f6f3bd`
- Pull request: `#7`
- Integrated as: `02d49d5bf97f20e0e9f7595971e17cd9c424761c`
- Added strict versioned catalog configuration, ranked preferred/backup source failover, DNS-pinned proxy-free ranged delivery verification, exact-origin redirect policy, canonical resolution results, safe attempt summaries, cancellation/job events, Delivery Links UI, and end-to-end CI
- Existing disconnected executor remains the default unless the verified resolver is explicitly configured

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`02d49d5bf97f20e0e9f7595971e17cd9c424761c`

This branch is the cumulative non-production integration line for all remaining work.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, credentials, or mounted volumes; those checks must be executed on the VPS during release-candidate and final-cutover verification.

## Remaining work

1. Movies, TV, Missing, Recently Added, and Jellyfin integration with deterministic non-production fixtures and explicit external configuration
2. Admin authentication, authorization boundaries, source management, diagnostics, and audit-safe events
3. PostgreSQL durable schema, repository layer, migrations, and SQLite import/rollback tooling
4. Production Docker images and Compose topology, health/readiness checks, Caddy routing, secrets handling, and least-privilege runtime settings
5. Structured observability, metrics, tracing boundaries, operational diagnostics, backup, restore, and recovery drills
6. Full behavioral parity, load, security, migration, rollback, and release-candidate verification
7. Final controlled deployment and traffic switch after explicit user action

## Working method

- Continue through internal checkpoints without requiring the user to copy large milestone prompts.
- Use focused branches and pull requests into `restructure/integration`.
- Require green CI before each integration merge.
- Keep `master` and production untouched until a final release candidate is complete.
- Stop only for an actual external blocker, missing production-only information, or the final VPS deployment/cutover action.

## Next action

Create the next focused branch from `restructure/integration` and implement the library-domain foundation for Movies, TV, Missing, and Recently Added. Keep Jellyfin behind an explicit credential-free interface and sanitized mock server first; define canonical contracts, durable-boundary interfaces, deterministic fixture parity, cancellation/timeouts, and network-safety rules before adding any real external configuration. Do not contact a live Jellyfin server, change production routing, or touch port 8765.
