---
name: check-live-endpoints
description: Verify the unofficial Goodreads endpoints this server depends on are still working, and identify which surface broke. Use when a tool returns empty or wrong data, before a release, or when you suspect Goodreads changed its markup, WAF posture, or GraphQL config.
---

# Check live endpoints

This server rides five unofficial Goodreads surfaces. They break without
notice, and the offline test suite runs on fixtures so it cannot detect it.
This skill checks the real thing.

## Run the live suite

```bash
GOODREADS_LIVE=1 pytest tests/e2e -v
```

Offline passing + live failing *suggests* an upstream change, but it isn't
proof — the offline suite runs on fixtures, so it can equally miss a local
regression in request construction or parsing. Before concluding Goodreads
moved, rule out:

- **a recent local change** — `git log` the tool's code path; re-run the live
  test at the previous commit if there's any doubt
- **transient network failure** — re-run once
- **rate limiting** — repeated 429/503 can exhaust the retries and surface as
  a parse failure downstream

## Read the failure by surface

| symptom | surface | what happened |
|---|---|---|
| `WAFChallenge` raised | HTML pages | AWS WAF now gates that path (HTTP 202) |
| `LoginRequired` raised | HTML pages | the path redirected to `/user/sign_in` (the review-list page did in Sep 2026, #91), or the profile is private |
| `GraphQLError`, or 401/403 | AppSync GraphQL | key or endpoint rotated |
| `ValueError: No __NEXT_DATA__ blob` | page JSON | page is no longer Next.js, or is WAF-gated |
| fields present but `None` | page JSON / GraphQL | Apollo state keys or schema fields renamed |
| RSS returns no items | shelf RSS | shelf went private, or feed shape changed |
| `list_shelves` returns `[]` | scraped HTML | `shelf=`/`tag=` links gone from the bookshelves module of `/user/show/{uid}` |

## Confirm GraphQL config still resolves

```bash
python -c "
from goodreads_mcp.client import GoodreadsClient
e, k = GoodreadsClient().graphql_config(force=True)
print(e); print(k[:8] + '...')
"
```

Expect an `appsync-api` endpoint ending in `/graphql` and a key starting
`da2-`. If this fails, the discovery code in `client.py` needs updating:
`APP_CHUNK_RE`, `NEXT_DATA_RE`, `APPSYNC_KEY_RE`, `APPSYNC_ENDPOINT_RE` and
`APPSYNC_PAIR_RE`, plus the `parse_page_api_key`, `parse_appsync_endpoint` and
`parse_appsync_config` parsers around them. Start with `parse_appsync_endpoint`
— it is on the primary path, while `parse_appsync_config` and its paired
`APPSYNC_PAIR_RE` only run as the legacy fallback. Never respond by hardcoding
a key or endpoint.

## After fixing

Add or update an offline fixture test covering the new shape, so the next
occurrence is caught by `pytest -q`. Then re-run both suites.
