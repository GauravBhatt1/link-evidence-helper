# Data-preservation boundary

Milestone 1 must not read or write production SQLite, adapter configuration,
media roots, Jellyfin data, environment files, or production Docker volumes.

Before any later data migration:

1. Back up SQLite DB, WAL and SHM consistently.
2. Back up and checksum `/data/adapters`.
3. Inventory tables, counts, relationships, roots, settings and integrations.
4. Exclude stale signed URLs and secret-bearing transient data.
5. Import transactionally into staging PostgreSQL tables.
6. Validate counts, relationships and deterministic hashes.
7. Produce secret-masked reports and tested rollback instructions.
