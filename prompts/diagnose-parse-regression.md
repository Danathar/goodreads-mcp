# Diagnose a parsing regression

Goodreads changes its markup and API shapes without notice. This is the most
common way this server breaks, and offline tests cannot catch it — they run on
fixtures.

---

A goodreads-mcp tool is returning wrong or empty data. Diagnose it.

1. Reproduce against the live site, not fixtures:
   `GOODREADS_LIVE=1 pytest tests/e2e -v -k <tool_name>`
   Offline passing + live failing points at an upstream change, but rule out
   the cheaper explanations first: a recent local change to request
   construction or parsing that the fixtures don't represent, a transient
   network failure, or rate limiting (429/503 exhausting the retries).
   Re-run once, and check `git log` on the tool's code path before concluding
   Goodreads moved.

2. Identify which surface the tool uses (shelf RSS, JSON autocomplete,
   `__NEXT_DATA__`, AppSync GraphQL, or — for `list_shelves` only — a
   best-effort HTML scrape) — see AGENTS.md.

3. Check the usual suspects in order:
   - `WAFChallenge` raised → the path is now WAF-gated. Find an alternate
     endpoint (`.xml` book page, RSS, JSON autocomplete). Do not try to solve
     the challenge.
   - `GraphQLError` or 401/403 → key/endpoint rotation. `graphql_config`
     should self-heal; if it doesn't, the discovery regexes in `client.py`
     need updating.
   - Empty/None fields from `__NEXT_DATA__` → Apollo state keys were renamed.
     Fetch the page, dump the blob, and diff the shape against what the
     parser expects.
   - `list_shelves` empty → it regexes `shelf=` and `tag=` params out of the
     HTML of the profile page `/user/show/{uid}`, so it's the most
     markup-fragile tool here. Fetch that page and check whether the
     bookshelves module still links shelves with a `shelf=` or `tag=` query
     param. A private profile and a sign-in redirect raise `LoginRequired`
     rather than returning `[]` (#91); if you see `[]` for a public user,
     the links moved.

4. Fix the parser, then add or update an offline fixture test covering the new
   shape so the regression is caught next time.

5. Re-run both suites: `pytest -q` and `GOODREADS_LIVE=1 pytest tests/e2e -v`.

Constraints: read-only, no auth, don't increase request rate.
