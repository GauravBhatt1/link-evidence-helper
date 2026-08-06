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

### Milestones 19–23 — PostgreSQL persistence foundations

- Versioned reversible PostgreSQL migrations for credential-free administrative sources and bounded audit events, with database constraints for allowlisted values, optimistic revisions, safe endpoint structure, UTC-aware timestamps, and exclusion of credential-bearing fields.
- Pull request `#30`: deterministic migration planner that validates paired contiguous up/down migrations and exposes checksum-addressed forward and rollback plans without opening a database or executing SQL.
- Pull request `#31`: context-aware durable source/audit repository interfaces and an explicit atomic transaction boundary, with PostgreSQL selection and connection opening kept outside package initialization.
- Pull request `#33`: transactional PostgreSQL source registry with bounded query timeouts, domain validation, normalized endpoints, optimistic revision updates, deterministic conflict mapping, explicit commit/rollback behavior, unique-violation mapping, and a database/sql adapter that never opens a connection implicitly.
- Pull request `#36`: bounded PostgreSQL audit repository that revalidates the secret-safe audit contract, normalizes timestamps to UTC, maps duplicate event identifiers deterministically, and accepts only an explicitly injected pool or transaction.

The overlapping draft repository implementations in pull requests `#32` and `#35` were closed as superseded. PostgreSQL runtime selection remains disabled; no driver connection string, credentials, production data, deployment, or migration execution is configured.

## Active checkpoint

Pull request `#37` adds the explicit migration runner and PostgreSQL advisory-lock store. It validates contiguous durable history, rejects checksum drift before mutation, applies only missing migrations, rolls back exactly the latest migration, uses a dedicated connection-scoped non-blocking advisory lock, and wraps every migration plus history update in a serializable transaction. Runtime invocation remains absent and disabled by default until this pull request passes all relevant CI and is merged.

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`a10b6f63d688171b3af1cac5e115f86f611ed6d1`

This branch is the cumulative non-production integration line for all remaining work.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, credentials, or mounted volumes; those checks must be executed on the VPS during release-candidate and final-cutover verification.

## Remaining work

1. Complete and merge the migration runner/locking checkpoint, then add a disabled-by-default PostgreSQL runtime configuration boundary and SQLite import/rollback tooling
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

Finish pull request `#37`, merge it only after all relevant CI is green, then create a focused branch for an explicit disabled-by-default PostgreSQL runtime configuration boundary. The runtime boundary must validate configuration without logging connection strings, must not connect implicitly during package initialization, and must keep migration execution as a separate operator action. After that, add dry-run-first SQLite import and rollback tooling with fixture-only tests. Do not add production credentials, import live data, deploy, modify `master`, access the VPS, change production routing, or touch port 8765.
