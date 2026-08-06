# Backup and recovery safety boundary

This checkpoint defines deterministic, repository-only backup and recovery planning. It does not connect to PostgreSQL or SQLite, execute `pg_dump`, copy files, delete artifacts, or restore data.

## Backup manifest

Each backup plan has a bounded lowercase identifier, an explicit UTC creation time, and one or more sorted artifacts. Every artifact records a closed kind, bounded name, byte size, and SHA-256 digest. Duplicate artifact names and malformed digests are rejected before a plan is accepted.

## Integrity verification

Artifact verification compares both the declared byte size and SHA-256 digest. Error responses are generic and do not include artifact content, connection strings, paths, credentials, or backend output.

## Retention planning

Retention is planning-only. A policy must keep at least one and no more than 90 backups, with a maximum age no greater than 365 days. The planner deterministically returns separate keep and delete lists; it performs no deletion.

## Restore planning

Restore plans are always created with `Executable: false`. Enabling an actual restore requires a later, separately reviewed runtime boundary with explicit operator confirmation, a verified backup manifest, an isolated target, pre-restore backup, rollback evidence, and production-only credentials supplied outside the repository.

## Release safety

No command in this checkpoint modifies a database, filesystem, container volume, VPS, DNS, routing, certificates, `master`, production traffic, or port `8765`.
