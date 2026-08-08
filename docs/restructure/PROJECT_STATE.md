# Restructuring project state

Last updated: 2026-08-08

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
- Deterministic dry-run SQLite source import, reverse-order rollback planning, and tamper-evident review sealing without database access or execution.

### Milestones 26–30 — runtime and operational safety foundations

- Secret-safe liveness and bounded readiness handlers with deterministic, generic responses.
- Multi-stage distroless non-root API and worker images.
- Loopback-only non-production Compose topology with internal Redis, optional PostgreSQL, runtime-injected secrets, read-only filesystems, dropped capabilities, no-new-privileges, bounded tmpfs mounts, health checks, image-build validation, and no production listener.
- Repository-only Caddy preview routing on loopback port `18781`, with automatic HTTPS and the Caddy admin API disabled, unsafe methods rejected, and no production hostname, certificate, DNS, or traffic changes.
- Closed, bounded structured logging, metrics, tracing, and request-correlation contracts.
- Deterministic backup manifests, integrity verification, retention planning, and always-non-executable restore plans without database, filesystem, or production access.

### Milestone 31 — repository-wide release-candidate gate

Completed and merged the fail-closed release-candidate verification workflow:

- race-enabled Go tests, formatting, vet, and API/worker builds
- locked TypeScript workspace installation, typechecking, contract tests, and React tests
- isolated Playwright browser-worker build and test execution
- diff checks rejecting production listeners, port `8765`, credential-shaped additions, and false deployment claims
- operator documentation separating repository readiness from controlled VPS deployment

The release-candidate workflow and all relevant checks passed before merge into `restructure/integration`.

## Current checkpoint

The repository restructuring line is ready for controlled deployment verification. No additional repository-only production requirement is currently known to be incomplete.

Follow-up VPS readiness rehearsal on 2026-08-08 fixed and verified two release-candidate gate gaps:

- API contract Go models are gofmt-clean.
- The loopback Caddy preview now starts under read-only rootfs, dropped capabilities, and `no-new-privileges:true`, and proxies `/healthz` successfully on port `18781`.

Repository readiness does not mean production has been changed. The final deployment remains intentionally blocked on production-only verification and explicit operator action.

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`0e3eec313fe0c1a18f3747efc2e2ce1d2d3a3355`

This branch is the cumulative non-production release-candidate line.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, credentials, mounted volumes, DNS, TLS state, backup destinations, or rollback behavior. Those checks must be executed on the VPS during the controlled deployment window.

## External deployment requirements

The next stage requires operator-controlled access to the production environment and runtime-only values. Do not commit any of these values to Git.

Required verification and inputs:

1. Confirm the current Python service, container/process identity, listener, health endpoint, volumes, database path, and rollback command.
2. Create and verify a fresh production backup using the documented backup and integrity process.
3. Supply runtime-only configuration for PostgreSQL, Redis, Jellyfin, administrator authentication, Caddy hostname/TLS, and any approved source integrations.
4. Run migration planning and dry-run import checks before any durable write.
5. Start the release candidate on non-production listeners and validate health, readiness, parity, browser fallback, Delivery Links, Jellyfin, admin operations, observability, backup, restore planning, and load behavior.
6. Execute rollback rehearsal without deleting or overwriting the existing production service or volumes.
7. Switch traffic only after all checks pass, then monitor and retain the old Python service for immediate rollback.

## Remaining work

Only production-environment execution remains:

- VPS inventory and secret-safe runtime configuration
- backup and restore verification
- migration dry run and controlled migration
- side-by-side release-candidate startup
- behavioral parity and load verification against the production oracle
- rollback rehearsal
- explicit traffic-switch decision

These tasks cannot be truthfully completed from GitHub alone and must not be simulated or represented as already deployed.

## Working method

- Keep `master` and production untouched until the controlled deployment step.
- Never commit production credentials or copied production data.
- Use the existing Python service as the behavioral oracle and rollback target.
- Stop immediately on backup, migration, parity, readiness, security, load, or rollback failure.
- Switch traffic only after explicit operator-controlled verification.

## Next action

Perform the documented controlled VPS deployment-readiness checklist with production access and runtime-only configuration. Do not merge to `master`, replace the live service, change DNS/certificates, or touch port `8765` before the side-by-side release candidate, backup, migration, parity, load, and rollback checks have all passed.
