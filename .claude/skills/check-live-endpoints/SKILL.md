---
name: check-live-endpoints
description: Verify the unofficial Goodreads endpoints this server depends on are still working, and identify which surface broke. Use when a tool returns empty or wrong data, before a release, or when you suspect Goodreads changed its markup, WAF posture, or GraphQL config.
---

# Check live endpoints

This server rides four unofficial Goodreads surfaces. They break without
notice, and the offline test suite runs on fixtures so it cannot detect it.
This skill checks the real thing.

## Run the live suite

```bash
GOODREADS_LIVE=1 pytest tests/e2e -v
```

Offline passing + live failing means Goodreads changed, not that the code is
wrong.

## Read the failure by surface

| symptom | surface | what happened |
|---|---|---|
| `WAFChallenge` raised | HTML pages | AWS WAF now gates that path (HTTP 202) |
| `GraphQLError`, or 401/403 | AppSync GraphQL | key or endpoint rotated |
| `ValueError: No __NEXT_DATA__ blob` | page JSON | page is no longer Next.js, or is WAF-gated |
| fields present but `None` | page JSON / GraphQL | Apollo state keys or schema fields renamed |
| RSS returns no items | shelf RSS | shelf went private, or feed shape changed |

## Confirm GraphQL config still resolves

```bash
python -c "
from goodreads_mcp.client import GoodreadsClient
e, k = GoodreadsClient().graphql_config(force=True)
print(e); print(k[:8] + '...')
"
```

Expect an `appsync-api` endpoint ending in `/graphql` and a key starting
`da2-`. If this fails, the discovery regexes in `client.py`
(`APP_CHUNK_RE`, `APPSYNC_PAIR_RE`, `parse_page_api_key`) need updating —
never respond by hardcoding a key or endpoint.

## After fixing

Add or update an offline fixture test covering the new shape, so the next
occurrence is caught by `pytest -q`. Then re-run both suites.
