# Project Operating Rules

## Goal

Build and maintain a production-ready source analyzer that discovers and verifies real download workflows across multiple qualities and hosts.

## Working mode

- Before every change, inspect the latest code, current Git branch, recent commits, open errors, and Docker deployment state.
- Continue from the repository state; do not ask the user to repeat history that is recorded here or visible in Git.
- Preserve working features. Prefer small, safe, testable changes.
- Make only evidence-backed claims. Never present a final URL unless its response was genuinely verified as downloadable.

## Architecture

- One Docker deployment; the production app listens on port `8765` only.
- Playwright is an internal rendering fallback; it has no public port.
- The analyzer traverses rendered and server HTML links, buttons, forms, `onclick` URLs, redirects, and queued branches.
- Keep qualities separate even when they share an intermediate URL. A blocked branch must never cancel other queued branches.

## Workflow and fallback policy

Expected path: movie page → quality → intermediate landing page → host → mirror → verified downloadable response.

When a branch reaches CAPTCHA, Turnstile, login, timeout, unsupported JavaScript, or other manual verification:

- mark that branch blocked with its reason;
- continue all remaining queued branches;
- do not attempt to solve or bypass the challenge.

Preferred host order when several choices are present:

1. DIRECT DOWNLOAD
2. VCLOUD DOWNLOAD
3. GDFLIX DOWNLOAD
4. Other detected mirrors

## Success criteria

A workflow is successful only after a real downloadable response is verified. Record the successful quality, host, and complete traversal/redirect log; report blocked and exhausted branches distinctly.

## Required development process

For each task:

1. Inspect latest repository and deployment state.
2. Reproduce the issue and identify the root cause.
3. Make the smallest reliable fix and add/update regression tests.
4. Run the relevant test suite.
5. Rebuild and deploy Docker.
6. Verify HTTP `200` on port `8765` and confirm no unexpected application port is listening.
7. Commit clearly, push to GitHub, and report root cause, files changed, tests, live result, deployment state, commit SHA, and any remaining blocker.

## Current known state

- Important traversal baseline: `dcb8d56` — `Traverse rendered download workflow branches`.
- Live Ikka 1080p traversal reached: movie page → `filesdl.live/view/3903` → GDFLIX → Cloudflare Turnstile → `Manual verification required`.
- This confirms `filesdl.live` is treated as an intermediate page. Current priority: ensure a blocked GDFLIX branch does not make the whole source fail while alternate queued hosts remain.

## Security

- Never store passwords, API keys, cookies, tokens, sessions, or other secrets in this file, commits, logs, or source code.
- Do not bypass CAPTCHA or Turnstile. Use only legitimate alternate hosts and normal browser behaviour.
