# Release-candidate verification gates

This document defines the repository-only checks required before any deployment rehearsal or production cutover may be considered.

## Automated gates

The `Release candidate gates` workflow verifies:

- all target application components and non-production deployment assets are present;
- protected production port `8765` is absent from non-production deployment and workflow assets;
- deployment and workflow files do not contain obvious literal credentials;
- all Go packages are formatted, race-tested, and vetted;
- canonical TypeScript contracts typecheck and pass tests;
- the React workflow typechecks and passes tests;
- the isolated Playwright browser worker typechecks and passes its Chromium tests.

These checks are intentionally repository-only. They do not access the VPS, production databases, live data, DNS, certificates, external storage, real credentials, or production traffic.

## Evidence still required before deployment

A release candidate is not deployment-ready until the following evidence is recorded separately:

1. Behavioral parity against the current Python service for supported search, selection, resolution, delivery, library, administration, and diagnostic flows.
2. A non-production Compose rehearsal with bounded load and failure recovery.
3. PostgreSQL migration rehearsal using disposable data, followed by checksum and rollback verification.
4. Backup artifact integrity verification and a non-destructive restore rehearsal.
5. Security review of authentication, source isolation, browser fallback, outbound request policy, secret handling, and observability cardinality.
6. A cutover runbook containing exact health checks, rollback triggers, owner, maintenance window, and confirmation that port `8765` remains untouched until the final traffic switch.

Passing this workflow is necessary but not sufficient for deployment. It must never be used as authorization to modify `master`, deploy to the live VPS, inject production credentials, change DNS or routing, or touch port `8765`.
