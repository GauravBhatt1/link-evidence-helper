// Package migrations exposes the versioned PostgreSQL schema without applying it implicitly.
package migrations

import "embed"

// Files contains reviewed up/down SQL migrations. Runtime code must select and
// apply migrations explicitly; importing this package never opens a database.
//
//go:embed *.sql
var Files embed.FS
