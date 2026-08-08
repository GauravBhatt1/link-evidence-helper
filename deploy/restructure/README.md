# Restructure deployment preview

These files define a repository-only, non-production preview topology. They do not deploy services, request certificates, update DNS, or change production traffic.

## Base preview

The base Compose model exposes the future Go API only on loopback port `18780` by default:

```sh
LINK_EVIDENCE_ADMIN_TOKEN='local-only-value-at-least-32-chars' \
  LINK_EVIDENCE_POSTGRES_PASSWORD='unused-without-durable-profile' \
  docker compose -f deploy/restructure/compose.yaml up --build
```

## Caddy web and routing preview

The optional Caddy overlay exposes Caddy only on loopback port `18781`, serves the built React web app at `/`, and proxies `/api/*` plus `/healthz` to the API over the isolated Compose frontend network:

```sh
LINK_EVIDENCE_ADMIN_TOKEN='local-only-value-at-least-32-chars' \
  LINK_EVIDENCE_POSTGRES_PASSWORD='unused-without-durable-profile' \
  docker compose \
    -f deploy/restructure/compose.yaml \
    -f deploy/restructure/compose.caddy.yaml \
    up --build
```

The preview Caddyfile deliberately disables automatic HTTPS and the Caddy admin API. It contains no domain, certificate issuer, DNS provider, credentials, or production listener. The existing production service and port `8765` are outside this topology.

The Caddy overlay builds a preview-only image from `Dockerfile.caddy-preview`.
The image compiles the web application with same-origin API transports and
removes Caddy's inherited low-port file capability so the container can keep
`no-new-privileges:true` while listening only on the internal high port `8080`.

## Legacy bridge preview

For side-by-side production parity checks, the preview API and worker can call
the still-running Python service through Docker's host gateway. This keeps
browser traffic on preview port `18781` while the legacy service remains the
behavioral oracle on port `8765`:

```sh
LINK_EVIDENCE_ADMIN_TOKEN='local-only-value-at-least-32-chars' \
  LINK_EVIDENCE_POSTGRES_PASSWORD='unused-without-durable-profile' \
  LINK_EVIDENCE_SEARCH_MODE=legacy-bridge \
  LINK_EVIDENCE_RESOLUTION_MODE=legacy-bridge \
  LINK_EVIDENCE_LEGACY_BASE_URL=http://host.docker.internal:8765 \
  LINK_EVIDENCE_LEGACY_ALLOW_NON_LOOPBACK=true \
  docker compose \
    -f deploy/restructure/compose.yaml \
    -f deploy/restructure/compose.caddy.yaml \
    up --build
```

If the Python service was started with an app token, provide it only at runtime
with `LINK_EVIDENCE_LEGACY_ACCESS_TOKEN`. Do not commit runtime tokens.

Before any controlled deployment, create a separate release-candidate configuration with an explicitly reviewed hostname, certificate strategy, trusted-proxy policy, request-size limits, and rollback procedure. Do not reuse the preview Caddyfile as a production cutover configuration.
