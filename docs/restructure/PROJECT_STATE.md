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

### Milestones 9–11 — library domain, API/UI, and Jellyfin connector

- Pull requests: `#9`, `#10`, and `#11`
- Added versioned library contracts, deterministic fixtures, repository boundaries, strict same-origin library APIs, typed React views, and an explicit opt-in Jellyfin connector.
- Jellyfin access is bounded, redirect-safe, proxy-isolated, credential-free in URLs, cache-aware, cancellation-aware, and disabled unless runtime configuration is supplied.

### Milestones 12–14 — admin authentication, audit contracts, and source registry

- Pull requests: `#13`, `#14`, `#15`, and `#16`
- Added runtime-only administrator bearer authentication, constant-time verification, fail-closed session handling, bounded secret-safe audit events, and a concurrency-safe source registry with optimistic revisions.
- Tokens, cookies, headers, arbitrary metadata, request bodies, and production source credentials are excluded from committed state and audit payloads.

### Milestone 15 — authenticated source-management HTTP boundary

- Pull requests: `#21` and `#22`
- Added fail-closed administrator authorization, strict JSON/content-type/body-size handling, unknown-field rejection, deterministic revision-conflict responses, query-parameter rejection, and an explicit disabled-by-default development runtime switch.
- No live source configuration, durable persistence, production routing, or deployment is enabled.

### Milestone 16 — secret-safe diagnostics boundary

- Pull requests: `#20` and `#25`
- Added allowlisted diagnostic snapshots and an administrator-only diagnostics endpoint that remains disabled unless an explicit provider is supplied.
- Provider failures map to generic responses; URLs, hostnames, credentials, headers, cookies, and arbitrary backend details are not exposed.

### Milestone 17 — bounded audit recording and source-mutation outcomes

- Pull requests: `#24` and `#27`
- Added a bounded concurrency-safe in-memory audit recorder and source create/update/disable outcome recording.
- Audit storage remains non-durable and excludes request bodies, endpoints, headers, cookies, tokens, credentials, and arbitrary metadata by construction.

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`5c0749393ab17c7f0cfcfa926dfda9519a43dedf`

This branch is the cumulative non-production integration line for all remaining work.

## Current focused work

Pull request `#28` on `restructure/postgres-schema-foundation` adds the first PostgreSQL migration foundation for credential-free administrative sources and bounded audit events. It embeds versioned up/down SQL without applying migrations implicitly and adds path-scoped schema safety tests. It must not be merged until all relevant CI is green.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, credentials, or mounted volumes; those checks must be executed on the VPS during release-candidate and final-cutover verification.

## Remaining work

1. Complete PostgreSQL migrations, durable repositories, transaction boundaries, migration locking, and SQLite import/rollback tooling
2. Add production Docker images and Compose topology, health/readiness checks, Caddy routing, secret handling, and least-privilege runtime settings
3. Add structured observability, metrics and tracing boundaries, backup, restore, and recovery drills
4. Complete behavioral parity, load, security, migration, rollback, and release-candidate verification
5. Perform the final controlled deployment and traffic switch only after explicit user action

## Working method

- Continue through internal checkpoints without requiring milestone approvals.
- Use focused branches and pull requests into `restructure/integration`.
- Require green relevant CI before each integration merge.
- Keep `master` and production untouched until a final release candidate is complete.
- Stop only for an actual external blocker, missing production-only information, or the final VPS deployment/cutover action.

## Next action

Finish and verify pull request `#28`. After it is green and merged, create a focused PostgreSQL repository branch that adds explicit runtime-only database configuration, bounded connection settings, transactional source-registry persistence, deterministic optimistic-concurrency behavior, integration tests against an ephemeral PostgreSQL service, and fail-closed startup behavior. Do not add production credentials, execute migrations against a live database, deploy, modify `master`, access the VPS, or change port 8765.
