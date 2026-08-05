# Admin authentication runtime boundary

Administrative API access is disabled by default. No password, token, hash, or example secret is committed to the repository.

## Enable on a trusted runtime

Generate a random token with at least 32 characters and provide it only through the process environment:

```bash
export LINK_EVIDENCE_ADMIN_TOKEN='replace-with-a-random-runtime-secret'
```

Do not place the token in Git, Docker images, frontend environment variables, browser bundles, URLs, query parameters, screenshots, or logs.

The current protected probe is:

```text
GET /api/v1/admin/session
Authorization: Bearer <runtime token>
```

Behavior:

- no token configured: `503 admin_auth_unavailable`
- missing or incorrect credential: `401 unauthorized`
- correct credential: `200` with `{ "ok": true, "success": true, "role": "admin" }`

The API stores only a SHA-256 digest in memory and compares credentials in constant time. Future administrative mutation routes must use this same fail-closed boundary. This feature does not create browser sessions, cookies, users, password recovery, or public registration.
