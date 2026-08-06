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

### Milestones 0–18 — architecture through authenticated operations

Completed architecture and migration design, canonical contracts, isolated React shell, fixture search workflow, loopback-only Go Search API, Redis job foundation, hardened HTTP-first shadow search, isolated Playwright fallback, verified Delivery Links workflow, library contracts and fixture views, secure Jellyfin connector boundaries, fail-closed administrator authentication, bounded secret-safe audit events, source administration, diagnostics, and mutation auditing.

### Milestones 19–25 — PostgreSQL and legacy import safety foundations

- Versioned reversible PostgreSQL migrations for credential-free administrative sources and bounded audit events.
- Deterministic checksum-addressed migration planning and locked serializable migration execution.
- Context-aware durable source/audit repository interfaces and explicit atomic transaction boundaries.
- Transactional PostgreSQL source registry and bounded PostgreSQL audit repository.
- Explicit disabled-by-default PostgreSQL runtime configuration validation without implicit connections or migration execution.
- Pull request `#41`: deterministic dry-run SQLite source import and reverse-order rollback planning with fixture-only tests. It opens no database, reads no live file, executes no SQL, and mutates no repository.

## Active checkpoint

Branch `restructure/runtime-health-boundary` adds a secret-safe HTTP health boundary for the future Go runtime:

- stable liveness responses that do not depend on external systems
- bounded readiness checks supplied explicitly by runtime composition
- generic unavailable responses that never expose backend errors or connection details
- query rejection, GET/HEAD-only behavior, no-store headers, deterministic check ordering, race-enabled tests, and focused CI

The package is not mounted into a production server and does not open Redis, PostgreSQL, SQLite, network, or VPS connections.

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`8cb6140dbc19f6432af16a1ecafe4bb2782ead4f`

This branch is the cumulative non-production integration line for all remaining work.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, credentials, or mounted volumes; those checks must be executed on the VPS during release-candidate and final-cutover verification.

## Remaining work

1. Merge the runtime health boundary only after all relevant CI is green, then build production Docker images and a non-production Compose topology with least-privilege defaults and explicit secrets injection
2. Add Caddy configuration templates and routing verification without switching live traffic
3. Add structured observability, metrics, tracing boundaries, backup, restore, and recovery drills
4. Complete behavioral parity, load, security, migration, rollback, and release-candidate verification
5. Perform final controlled deployment and traffic switch only after explicit user action

## Working method

- Continue through internal checkpoints without requiring milestone approvals.
- Use focused branches and pull requests into `restructure/integration`.
- Require green relevant CI before each integration merge.
- Keep `master` and production untouched until a final release candidate is complete.
- Stop only for an actual external blocker, missing production-only information, or the final VPS deployment/cutover action.

## Next action

Open the runtime-health pull request, merge it only after every relevant check is green, then create a focused branch for production-oriented but non-deployed container images and Compose topology. Keep all services bound away from the existing production listener, require runtime-injected secrets, use read-only filesystems and non-root users where practical, and add configuration validation tests. Do not deploy, modify `master`, access the VPS, switch routing, add credentials, import live data, or touch port 8765.
