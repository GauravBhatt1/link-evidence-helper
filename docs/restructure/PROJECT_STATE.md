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

### Milestones 9–11 — library domain, fixture views, and secure Jellyfin connector

Added versioned library contracts, fixture-backed API and React views, and an explicit runtime-only Jellyfin connector with bounded pagination, DNS and redirect safety, response limits, caching, cancellation, and mock-server coverage. Fixture mode remains the default; no live credentials or production data are committed.

### Milestones 12–14 — administrator authentication, audit events, and source registry

Added a fail-closed runtime-only administrator bearer-token boundary, bounded secret-safe audit events, and a concurrency-safe non-durable source registry with optimistic revisions and credential-free endpoint contracts.

### Milestones 15–18 — authenticated source administration and operational read boundaries

- Pull request `#21`: authenticated source-management HTTP routes with strict JSON/content-type/body limits, query rejection, deterministic conflicts, and secret-leak tests.
- Pull request `#22`: explicit development-only source-admin runtime mounting, disabled by default and requiring administrator authentication.
- Pull requests `#20` and `#25`: bounded diagnostics snapshot foundation and authenticated diagnostics HTTP boundary, both disabled unless explicitly supplied.
- Pull request `#24`: bounded concurrency-safe in-memory audit recorder.
- Pull request `#27`: secret-safe audit outcome recording for authenticated source create, update, and disable operations.

No production source configuration, durable database, live infrastructure probing, deployment, routing, or VPS mutation is enabled by these checkpoints.

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`5c0749393ab17c7f0cfcfa926dfda9519a43dedf`

This branch is the cumulative non-production integration line for all remaining work.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, credentials, or mounted volumes; those checks must be executed on the VPS during release-candidate and final-cutover verification.

## Remaining work

1. PostgreSQL durable schema, repositories, migrations, transaction boundaries, and SQLite import/rollback tooling
2. Production Docker images and Compose topology, health/readiness checks, Caddy routing, secrets handling, and least-privilege runtime settings
3. Structured observability, metrics, tracing boundaries, backup, restore, and recovery drills
4. Full behavioral parity, load, security, migration, rollback, and release-candidate verification
5. Final controlled deployment and traffic switch after explicit user action

## Working method

- Continue through internal checkpoints without requiring milestone approvals.
- Use focused branches and pull requests into `restructure/integration`.
- Require green relevant CI before each integration merge.
- Keep `master` and production untouched until a final release candidate is complete.
- Stop only for an actual external blocker, missing production-only information, or the final VPS deployment/cutover action.

## Next action

Create a focused branch from `restructure/integration` for the PostgreSQL persistence foundation. Add versioned, reversible SQL migrations for administrative sources and bounded audit events; define repository interfaces and transaction boundaries without enabling a production connection; keep runtime defaults non-durable and disabled; add migration validation and repository tests against an isolated CI database; and document import/rollback constraints. Do not add real credentials, copy production data, deploy, modify `master`, access the VPS, change production routing, or touch port 8765.
