# Canonical contract versioning

`packages/contracts/schema` is the sole canonical public-contract source.

Rules:

1. Schema IDs are versioned under `https://schemas.jobinfo.local/v1/`.
2. Public schemas use `additionalProperties: false`.
3. Source selectors, workflow internals, credentials, cookies, authorization
   headers and secret adapter configuration are forbidden.
4. Go and TypeScript representations must pass the same valid and invalid
   fixture corpus.
5. Breaking changes require a new schema/API version and a compatibility plan.
6. Golden fixtures are deterministic, sanitized, and carry provenance in
   `packages/testing/fixtures/manifest.json`.
