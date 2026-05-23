# ArkoseTracker

Automated tracker for Arkose Labs `api.js` across Roblox + other heavy
deployments (X, Uber, Match, Snap, Adobe). Built to feed live version /
build-id / enforcement-hash values into a downstream solver so the solver
doesn't break when Arkose rotates without warning.

A GitHub Action runs `tracker.py` every 15 minutes; when any endpoint's
enforcement hash changes, the new value lands in `versions.json` (and the
fetched `api.js` is archived under `data/`) within ~30 minutes.

## What gets tracked

For each endpoint:
- `public_key` — site key for that deployment
- `host` — Arkose CDN host serving the api.js
- `version` — semantic version pulled from the api.js body (e.g. `4.2.2`)
- `build_id` — UUID build identifier embedded in the api.js
- `enforcement_hash` — 32-char hex from the `enforcement.<HASH>.html` URL
- `previous_enforcement_hash` — the value before the most recent rotation
- `fetched_at`, `changed_at` — ISO-8601 UTC timestamps

## Consuming it from the solver

The SolverSystem solver fetches from:

```
https://raw.githubusercontent.com/<your-gh-username>/ArkoseTracker/main/versions.json
```

and refreshes once an hour. If the fetch fails (rate limit / outage), the
solver falls back to baked-in defaults so it keeps running.

## Why this exists

The `enforcement.<HASH>.html` URL is in the iframe `Referer` for every BDA
submission. When Arkose rotates the hash (which they do, frequently — often
within hours of a Chrome stable release) and your solver still references
the old hash, silent-pass rate drops to zero. This repo gives you a
self-healing pipe instead of a manual edit-and-redeploy cycle.

## Run locally

```bash
python tracker.py
```

Writes `versions.json` next to the script. No deps outside stdlib.
