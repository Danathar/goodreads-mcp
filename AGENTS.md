# AGENTS.md

Guidance for AI coding agents working in this repo. Other agent config files
(`CLAUDE.md`, `.github/copilot-instructions.md`, `.cursor/rules/`) point here —
edit this file, not the copies.

## Start and end of a session

**Read [`.claude/session-summary.md`](.claude/session-summary.md) first.** It
carries the state of play — what landed, what's in flight, what's blocked —
that would otherwise have to be rediscovered. **Overwrite it before you
finish**, so the next session starts where this one stopped.

Three layers, don't mix them up:

| file | holds | lifetime |
|---|---|---|
| `AGENTS.md` (this file) | standing rules | durable |
| [`.claude/memory/corrections.md`](.claude/memory/corrections.md) | things learned by getting them wrong | append-only |
| [`.claude/session-summary.md`](.claude/session-summary.md) | current state | overwritten each session |

## What this is

A read-only MCP server exposing public Goodreads data. Goodreads has had no
public API since Dec 2020, so every tool rides an unofficial read surface.

`goodreads_mcp/client.py` — HTTP, retry, WAF detection, GraphQL config
`goodreads_mcp/server.py` — the 12 `@mcp.tool` functions
`goodreads_mcp/config.py` — optional `GOODREADS_USER_ID` only

## Hard constraints

**Read-only, unauthenticated.** No login, no cookies, no writes, no
credentials. Tools are annotated `@mcp.tool(annotations=_READ_ONLY)`. Changes
that add write operations or require credentials are out of scope — say so
rather than implementing them.

**Be a polite guest.** These endpoints are unofficial. Keep the single shared
client and its session, keep exponential backoff on 429/503, keep the
browser-faithful headers. Don't add concurrency that multiplies request rate.
Tool calls run in worker threads so the server can answer pings mid-call
(`OffLoopFastMCP` in `server.py`, #92); `client.MAX_IN_FLIGHT` is what keeps
parallel calls from becoming parallel load. Raise it only with a reason.

## The data surfaces

In rough order of robustness:

1. **Shelf RSS** — `/review/list_rss/{user_id}?shelf=...`, structured XML
2. **Search autocomplete** — `/book/auto_complete?format=json&q=...`
3. **Embedded page JSON** — book pages are Next.js; `__NEXT_DATA__` carries
   the full Apollo state. **On these pages, parse the blob, never the markup.**
4. **AppSync GraphQL** — the discovery and review tools
5. **Scraped HTML** — `list_shelves` only, and explicitly best-effort: it
   regexes `shelf=` and `tag=` params out of the public profile page
   `/user/show/{uid}` (the review-list page `/review/list/{uid}` went
   login-only in Sep 2026, #91). There's no structured equivalent for shelf
   *names*, which is why this one exists. Don't extend this approach to
   anything that has a surface above it.

## Two things that will bite you

**The WAF.** Book HTML pages sit behind an AWS WAF JS challenge that returns
HTTP 202 with a body only a real browser can solve — so `raise_for_status()`
won't catch it. `get_book` routes around it via the `.xml`-suffixed page. If
you add a tool that fetches a book page, use `.xml`. The client detects
challenge bodies and raises `WAFChallenge` loudly rather than feeding
challenge markup to a parser; keep it that way.

**GraphQL config is resolved at runtime, deliberately.** The anonymous API key
comes from page-level `__NEXT_DATA__` and the endpoint from the `_app` JS
bundle (`client.graphql_config`), so key and endpoint rotations self-heal.
Never hardcode either. A 401/403 triggers one forced re-discovery and retry.

## Conventions

- GraphQL discovery tools page in batches of 20 (`_DISCOVERY_PAGE_SIZE`), cap
  `limit` at 100 (`_MAX_DISCOVERY`), and return `returned` / `has_more`. Use
  `_paginated_graphql_edges` rather than writing new pagination. `popular_books`
  and `compare_books` have their own tighter caps — check the constants near
  the top of `server.py` before assuming.
- Every result carries a source `url` so the model can cite it — see
  `SERVER_INSTRUCTIONS` in `server.py`. New tools should return `url` fields too.
- Partial GraphQL success is tolerated (a deleted sub-resource resolves to
  null); only a missing `data` raises `GraphQLError`.
- `pyproject.toml` and `manifest.json` versions must match — release CI fails
  if they drift.
- `pyproject.toml` is the only dependency list. The released `.mcpb` ships no
  packages: `manifest.json` launches with `uv run --directory ${__dirname}`,
  and the host resolves the bundled `pyproject.toml` on the user's machine.
  Don't vendor anything into the tree `mcpb pack` reads — a compiled module
  is built for one platform and Python, and release CI fails on one while the
  manifest declares several platforms (#89). Don't set `PYTHONPATH` in the
  manifest either; there is no portable list separator for it (#90).

## Writing issues, PRs and comments

A person who was not in your head will read what you write. Write for them.

- **Start with a plain summary**: two or three short sentences saying what
  changed (or what is wrong), why it matters, and what the reader should look
  at or do. Someone who reads only that should still get the point.
- In a PR, put the rest under the pull request template's headings.
- Short sentences. One idea per bullet. No nested dash-clauses.
- Use this project's own words. If you coin a term, define it the first time
  you use it.
- Tell the reader what they can check themselves: the command to run, the file
  to open, the number to compare.
- Keep the body under about 300 words. Long evidence (mutation lists, full test
  output, logs) goes in a collapsed `<details>` block.
- Issues: state the problem as a user would see it, then the evidence, then the
  proposed fix. The title says what is wrong, not what kind of finding it is;
  keep any prefix the tooling requires.

## Testing

```bash
pytest -q                                  # offline, fixture-based; what CI runs
GOODREADS_LIVE=1 pytest tests/e2e -v       # live, hits real Goodreads
```

CI enforces `--cov-fail-under=55` on `goodreads_mcp`.

**If you change parsing logic, run the live suite.** Offline tests use
fixtures and by construction cannot catch a Goodreads markup change — which is
this project's most common real failure.
