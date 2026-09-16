---
name: add-mcp-tool
description: Add a new read-only tool to the goodreads-mcp server following its existing patterns for annotations, pagination, caps, result shaping, and citation urls. Use when asked to expose new Goodreads data as an MCP tool.
---

# Add an MCP tool

Read [AGENTS.md](../../../AGENTS.md) first. Scope check before writing code:
this server is **read-only and unauthenticated**. If the request needs login,
cookies, or writes, say it's out of scope instead of implementing it.

## Pick the surface

In order of robustness: shelf RSS → JSON autocomplete → `__NEXT_DATA__` →
AppSync GraphQL. On Next.js book pages parse the embedded JSON, not the
markup, and fetch those pages via the `.xml`-suffixed path — the plain HTML
page is WAF-gated.

Scraping HTML is a last resort, not a pattern to copy. `list_shelves` does it
only because shelf *names* have no structured surface; if the data you need is
reachable from any surface above, use that instead.

## Write the tool

In `goodreads_mcp/server.py`:

```python
@mcp.tool(annotations=_READ_ONLY)
def my_tool(book_id: str, limit: int = 10) -> dict[str, Any]:
    ...
```

- Paginate with `_paginated_graphql_edges` — don't hand-roll it.
- Respect `_MAX_DISCOVERY` (100) and `_DISCOVERY_PAGE_SIZE` (20). Add a
  tighter cap if the tool fans out one request per item, as `compare_books`
  does with `_MAX_COMPARE`.
- Return `returned` and `has_more`.
- Shape results with `_book_summary` / `_work_summary` / `_node_summary` so
  tools stay chainable — one tool's `book_id` should feed the next.
- Every item needs a source `url`; `SERVER_INSTRUCTIONS` tells the model to
  cite from it. Null must be explicit, never fabricated.
- Resolve ids with `_resolve_book_ids` / `_legacy_id` rather than assuming a
  numeric id — slug forms like `11870085-the-fault-in-our-stars` are valid.

## Test

Offline fixture tests in `tests/`, a live test in
`tests/e2e/test_smoke_live.py`. CI runs `pytest -q --cov-fail-under=55`.

## Document

Add the tool to the table in `README.md` and the docstring list at the top of
`server.py`.
