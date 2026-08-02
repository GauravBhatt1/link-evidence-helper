# Database migrations

Milestone 1 creates documentation only. No PostgreSQL schema or SQLite import
has been implemented. Production SQLite remains the sole durable application
database.

Versioned PostgreSQL migrations and the tested `app migrate-from-sqlite`
command belong to Milestone 11 and require the preservation and rollback rules
in `docs/migration/data-preservation.md`.
