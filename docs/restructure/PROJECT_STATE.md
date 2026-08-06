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
- Deterministic dry-run SQLite source import, reverse-order rollback planning, and tamper-evident review sealing without database access or execution.

### Milestones 26–27 — runtime and container safety foundations

- Secret-safe liveness and bounded readiness handlers with deterministic, generic responses.
- Multi-stage distroless non-root API and worker images.
- Loopback-only non-production Compose topology with internal Redis, optional PostgreSQL, runtime-injected secrets, read-only filesystems, dropped capabilities, no-new-privileges, bounded tmpfs mounts, health checks, image-build validation, and no production listener.

## Active checkpoint

Branch `restructure/caddy-routing-foundation` adds a repository-only Caddy preview boundary:

- loopback-only preview listener on port `18781`
- routing exclusively to the future Go API over the Compose frontend network
- Caddy admin API and automatic HTTPS disabled
- no hostname, DNS provider, certificate issuer, credentials, production listener, or traffic switch
- security headers, unsafe-method rejection, active upstream health checking, static safety checks, Caddy validation, and merged Compose validation in focused CI

This checkpoint does not deploy Caddy, request certificates, update DNS, access the VPS, modify production traffic, or touch port `8765`.

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`6b5a11de842b617aec43604b830df819e2da9e1e`

This branch is the cumulative non-production integration line for all remaining work.

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production.

The known production service remains the existing Python application on port 8765. GitHub cannot independently verify the current VPS listener, container identity, local database, credentials, or mounted volumes; those checks must be executed on the VPS during release-candidate and final-cutover verification.

## Remaining work

1. Merge the Caddy routing foundation only after every relevant CI check is green
2. Add structured observability, metrics, and tracing boundaries without sensitive labels or payload capture
3. Add backup, restore, and recovery drill definitions with destructive actions disabled by default
4. Complete behavioral parity, load, security, migration, rollback, and release-candidate verification
5. Perform final controlled deployment and traffic switch only after explicit user action

## Working method

- Continue through internal checkpoints without requiring milestone approvals.
- Use focused branches and pull requests into `restructure/integration`.
- Require green relevant CI before each integration merge.
- Keep `master` and production untouched until a final release candidate is complete.
- Stop only for an actual external blocker, missing production-only information, or the final VPS deployment/cutover action.

## Next action

Open the Caddy routing pull request and merge it only after every relevant check is green. Then create a focused observability boundary with structured secret-safe logging, bounded metrics, and tracing interfaces that do not record URLs, headers, request bodies, cookies, tokens, connection strings, or arbitrary labels. Do not deploy, modify `master`, access the VPS, switch routing, add credentials, import live data, request certificates, change DNS, or touch port `8765`.
