# Restructuring project state

Last updated: 2026-08-06

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

### Milestones 0–8 — architecture, contracts, UI, search, jobs, browser fallback, and Delivery Links

Completed architecture and migration design, canonical contracts, isolated React shell, fixture search workflow, loopback-only Go Search API, Redis job foundation, hardened HTTP-first shadow search, isolated Playwright fallback, and verified resolution/Delivery Links workflow.

Key cumulative Delivery Links integration commit: `02d49d5bf97f20e0e9f7595971e17cd9c424761c`.

### Milestone 9 — library domain foundation

- Pull request: `#9`
- Added versioned library contracts and validated Go domain models for Movies, TV, Missing, Recently Added, seasons, episodes, and future Jellyfin boundaries.
- No live integration, persistence, production data, or external requests.

### Milestone 10 — fixture-backed library API and React views

- Pull request: `#10`
- Added deterministic shared fixtures, repository boundaries, strict same-origin library API, typed React transport, responsive library views, accessibility coverage, and privacy tests.

### Milestone 11 — explicit secure Jellyfin connector

- Pull request: `#11`
- Added opt-in runtime-only Jellyfin configuration, bounded pagination, canonical mapping, DNS and redirect safety, proxy isolation, response limits, caching, cancellation, deterministic mock-server tests, and operator documentation.
- Fixture mode remains the default; no live server or credential is committed or contacted by CI.

### Milestone 12 — administrator authentication boundary

- Pull requests: `#13` and follow-up `#16`
- Added runtime-only administrator bearer-token configuration, strict parsing, constant-time verification, fail-closed session endpoint, disabled-by-default behavior, route/runtime/security tests, dedicated CI, and secret-safe operator documentation.
- Tokens are not accepted through query strings, cookies, local storage, committed files, or logs.

### Milestone 13 — secret-safe audit event foundation

- Pull request: `#14`
- Added bounded administrative audit events with allowlisted actor, action, resource, and outcome values.
- Arbitrary metadata, headers, bodies, URLs, credentials, cookies, and tokens are excluded by construction.
- No production logging sink or durable database is enabled yet.

### Milestone 14 — bounded source-management registry foundation

- Pull request: `#15`
- Integrated as: `a8a4208bac59709b97a9bfd57be3db87a0f89f9e`
- Added concurrency-safe list/create/update/disable operations, optimistic revisions, deterministic ordering, credential-free endpoint contracts, strict kind and URL validation, UTC normalization, race-enabled tests, and dedicated CI.
- The in-memory registry is non-durable and is not wired into runtime implicitly; no live source configuration or mutation route is enabled.

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`a8a4208bac59709b97a9bfd57be3db87a0f89f9e`

This branch is the cumulative non-production integration line for all remaining work.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, credentials, or mounted volumes; those checks must be executed on the VPS during release-candidate and final-cutover verification.

## Remaining work

1. Authenticated and authorized source-management HTTP routes with strict request bounds, optimistic concurrency responses, CSRF-safe same-origin behavior, and audit-event emission
2. Diagnostics and operational read models that remain secret-safe and disabled unless explicitly configured
3. PostgreSQL durable schema, repositories, migrations, transaction boundaries, and SQLite import/rollback tooling
4. Production Docker images and Compose topology, health/readiness checks, Caddy routing, secrets handling, and least-privilege runtime settings
5. Structured observability, metrics, tracing boundaries, backup, restore, and recovery drills
6. Full behavioral parity, load, security, migration, rollback, and release-candidate verification
7. Final controlled deployment and traffic switch after explicit user action

## Working method

- Continue through internal checkpoints without requiring milestone approvals.
- Use focused branches and pull requests into `restructure/integration`.
- Require green relevant CI before each integration merge.
- Keep `master` and production untouched until a final release candidate is complete.
- Stop only for an actual external blocker, missing production-only information, or the final VPS deployment/cutover action.

## Next action

Create a focused branch from `restructure/integration` for authenticated source-management HTTP routes. Reuse the existing fail-closed administrator verifier, bounded source registry, and secret-safe audit event contract. Add strict JSON and body-size limits, reject query-string credentials and unsupported content types, map revision conflicts deterministically, keep the registry disabled unless explicitly supplied, and cover unauthorized, malformed, conflict, race, and secret-leak regression cases. Do not add live sources, persistence, production routing, deployment, VPS access, or port 8765 changes.
