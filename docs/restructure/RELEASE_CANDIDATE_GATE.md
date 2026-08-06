# Release candidate verification gate

This repository-only gate is the final pre-deployment quality boundary for the restructuring integration line. It does not deploy, publish images, access the VPS, connect to production data, change DNS or certificates, modify `master`, switch traffic, or bind port `8765`.

## Required evidence

A candidate may advance toward controlled deployment only when the `Release candidate gate` workflow and every other relevant pull-request check are green on the exact candidate commit.

The gate verifies:

- all Go API and worker packages format, build, pass race-enabled tests, and pass `go vet`
- TypeScript workspaces install from the committed lockfile and typecheck
- canonical contract and React unit tests pass
- the isolated Playwright browser worker builds and passes its tests
- the pull request targets only `restructure/integration`
- the candidate diff does not introduce production listeners, port `8765`, common credential material, or a claim that restructuring has already reached production

## Fail-closed behavior

Missing tests, build failures, dependency-lock drift, race failures, static-analysis failures, browser-worker failures, credential-shaped additions, or production-boundary changes fail the workflow. A green result is evidence for repository readiness only; it is not authorization to deploy.

## Controlled deployment remains separate

VPS-only checks still require an explicit controlled deployment step: listener and container identity, mounted volumes, production credentials, database backup and restore rehearsal, migration execution, rollback timing, health checks, and the final traffic switch. Those operations must not be performed by pull-request CI.
