# Restructuring project state

Last updated: 2026-08-06

## Goal

Deliver one production-ready repository and deployment path for the existing link-evidence-helper application while keeping the current Python production service available as the behavioral oracle until final parity and rollback verification are complete.

## Target architecture

- React + TypeScript web application
- Go API and workers
- isolated TypeScript + Playwright browser workers
- Redis for ephemeral coordination
- PostgreSQL for durable application state
- Caddy for final routing and TLS
- existing Python application retained until verified cutover

## Permanent safety rules

- Never expose secrets, credentials, cookies, signed URLs, or production data in Git history.
- Never delete production volumes or use destructive Git reset commands for rollback.
- Keep live production traffic on the current Python application until full parity, health, migration, and rollback checks pass.
- Browser automation remains isolated and fallback-only.
- Do not present fixture, mock, or disconnected behavior as live production functionality.

## Completed checkpoints

### Milestones 0–18 — architecture through authenticated operations

Completed architecture and migration design, canonical contracts, isolated React shell, fixture workflow, loopback-only Go Search API, Redis job foundation, hardened HTTP-first search, isolated browser fallback, delivery and library contracts, secure Jellyfin boundaries, fail-closed administrator authentication, bounded audit events, source administration, diagnostics, and mutation auditing.

### Milestones 19–27 — persistence, import, and runtime safety foundations

- Reversible PostgreSQL migrations, deterministic planning, locked serializable execution, durable repository contracts, transactional source registry, and bounded audit repository.
- Disabled-by-default PostgreSQL runtime configuration validation without implicit connections.
- Dry-run SQLite import and reverse-order rollback planning.
- Tamper-evident SQLite import review sealing merged in PR `#43` after green CI.
- Secret-safe liveness and bounded readiness boundary merged in PR `#44` after green CI.

## Active checkpoint

Branch `restructure/nonprod-container-topology` adds a production-oriented but non-deployed container foundation:

- multi-stage, distroless, non-root Go API image
- isolated Compose network with no host-published ports
- read-only filesystems, dropped capabilities, no-new-privileges, bounded tmpfs mounts, and health checks
- disabled-by-default PostgreSQL profile with externally supplied file-backed secret
- pinned service image versions and static configuration regression checks
- CI that validates the topology, renders Compose configuration, and builds the API image

This checkpoint does not deploy, access the VPS, publish port 8765, inject a real credential, or switch traffic.

## Current integration branch

`restructure/integration`

Current recorded implementation commit:

`e33c423acede5882080a5693495815dcdf512fe3`

## Current production boundary

The default branch is `master`. No restructuring work has been merged to `master`, deployed, or routed to production. The known production service remains the existing Python application on port 8765; VPS-only verification is reserved for the controlled release-candidate step.

## Remaining work

1. Merge the non-production container topology only after all relevant CI is green.
2. Add Caddy templates and offline routing verification without switching live traffic.
3. Add structured observability, metrics/tracing boundaries, backup, restore, and recovery drills.
4. Complete behavioral parity, load, security, migration, rollback, and release-candidate verification.
5. Perform final controlled deployment and traffic switch only after explicit user action.

## Working method

- Continue through internal checkpoints without milestone approvals.
- Use focused branches and pull requests into `restructure/integration`.
- Require green relevant CI before every integration merge.
- Keep `master`, production, secrets, the VPS, and port 8765 untouched.

## Next action

Open and validate the non-production container-topology pull request. Merge it only after every relevant check is green, then create a focused branch for Caddy configuration templates and offline routing tests. Do not deploy, modify `master`, access the VPS, switch routing, add credentials, import live data, or touch port 8765.
