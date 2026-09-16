# Diagnose a parsing regression

Goodreads changes its markup and API shapes without notice. This is the most
common way this server breaks, and offline tests cannot catch it — they run on
fixtures.

---

A goodreads-mcp tool is returning wrong or empty data. Diagnose it.

1. Reproduce against the live site, not fixtures:
   `GOODREADS_LIVE=1 pytest tests/e2e -v -k <tool_name>`
   If the offline suite passes but the live suite fails, it's an upstream
   change, not a logic bug.

2. Identify which of the four surfaces the tool uses (shelf RSS, JSON
   autocomplete, `__NEXT_DATA__`, or AppSync GraphQL) — see AGENTS.md.

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

4. Fix the parser, then add or update an offline fixture test covering the new
   shape so the regression is caught next time.

5. Re-run both suites: `pytest -q` and `GOODREADS_LIVE=1 pytest tests/e2e -v`.

Constraints: read-only, no auth, don't increase request rate.
