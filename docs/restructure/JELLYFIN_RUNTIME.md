# Jellyfin runtime connector

The Go API keeps fixture mode as the default. Jellyfin is contacted only when the operator explicitly selects `jellyfin` library mode and supplies runtime-only credentials.

## Runtime variables

```env
LINK_EVIDENCE_LIBRARY_MODE=jellyfin
LINK_EVIDENCE_JELLYFIN_URL=https://media.example/jellyfin
LINK_EVIDENCE_JELLYFIN_API_KEY=replace-at-runtime
LINK_EVIDENCE_JELLYFIN_LIBRARY_IDS=
LINK_EVIDENCE_JELLYFIN_ALLOW_PRIVATE=false
LINK_EVIDENCE_JELLYFIN_TIMEOUT_SECONDS=10
LINK_EVIDENCE_JELLYFIN_PAGE_SIZE=200
LINK_EVIDENCE_JELLYFIN_MAX_ITEMS=5000
LINK_EVIDENCE_JELLYFIN_CACHE_SECONDS=30
```

`LINK_EVIDENCE_JELLYFIN_LIBRARY_IDS` is an optional comma-separated list of Jellyfin parent library IDs. When omitted, the connector requests all supported library items visible to the API key.

Jellyfin commonly runs on a local address such as port 8096. Private and loopback targets remain blocked unless `LINK_EVIDENCE_JELLYFIN_ALLOW_PRIVATE=true` is explicitly set. That switch should only be used when the operator controls the destination network.

## Security boundary

- The API key is sent only in the server-side `X-Emby-Token` request header.
- Credentials are never placed in URLs, public contracts, browser responses, fixtures, logs, or Git history.
- Only HTTP and HTTPS base URLs are accepted; URL user information, query strings, fragments, and scoped IP literals are rejected.
- Environment proxies are disabled.
- DNS is resolved and validated before dialing; unsafe, unspecified, multicast, link-local, documentation, benchmarking, and carrier-grade NAT ranges are rejected.
- Redirects are rejected so credentials cannot move to another origin.
- Responses require JSON, are size-limited, paginated, item-limited, and validated before entering the public library model.
- Remote poster images and filesystem paths are not requested or exposed.
- The connector supports cancellation and a bounded in-memory snapshot cache.

## Public behavior

The public library endpoint remains:

```text
GET /api/v1/library?view=movies
GET /api/v1/library?view=tv
GET /api/v1/library?view=missing
GET /api/v1/library?view=recent
```

Jellyfin returns present items, so connected-mode items are marked `available`. A durable desired-library catalogue is still required before connected mode can infer truly missing titles or episodes.

## Deployment note

Do not commit the real API key to an `.env` file in the repository. Supply it through the final deployment secret mechanism. Fixture mode remains suitable for CI and local UI work without contacting a Jellyfin server.
