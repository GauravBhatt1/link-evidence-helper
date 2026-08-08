# Restructure deployment preview

These files define a repository-only, non-production preview topology. They do not deploy services, request certificates, update DNS, or change production traffic.

## Base preview

The base Compose model exposes the future Go API only on loopback port `18780` by default:

```sh
LINK_EVIDENCE_ADMIN_TOKEN='local-only-value' \
  docker compose -f deploy/restructure/compose.yaml up --build
```

## Caddy routing preview

The optional Caddy overlay exposes Caddy only on loopback port `18781` and proxies to the API over the isolated Compose frontend network:

```sh
LINK_EVIDENCE_ADMIN_TOKEN='local-only-value' \
  docker compose \
    -f deploy/restructure/compose.yaml \
    -f deploy/restructure/compose.caddy.yaml \
    up --build
```

The preview Caddyfile deliberately disables automatic HTTPS and the Caddy admin API. It contains no domain, certificate issuer, DNS provider, credentials, or production listener. The existing production service and port `8765` are outside this topology.

The Caddy overlay builds a preview-only image from `Dockerfile.caddy-preview`.
It removes Caddy's inherited low-port file capability so the container can keep
`no-new-privileges:true` while listening only on the internal high port `8080`.

Before any controlled deployment, create a separate release-candidate configuration with an explicitly reviewed hostname, certificate strategy, trusted-proxy policy, request-size limits, and rollback procedure. Do not reuse the preview Caddyfile as a production cutover configuration.
