# Add a GraphQL-backed discovery tool

---

Add a new discovery tool to goodreads-mcp. Follow the existing patterns in
`goodreads_mcp/server.py` rather than inventing new ones. Read AGENTS.md first.

Requirements:

- Decorate with `@mcp.tool(annotations=_READ_ONLY)`. This server is read-only;
  if the feature needs auth or writes, stop and say so.
- Use `_paginated_graphql_edges` for pagination. Don't hand-roll it — every
  supported connection uses Goodreads' standard `PaginationInput` / `PageInfo`
  shapes.
- Respect the caps: `_MAX_DISCOVERY` (100) total, `_DISCOVERY_PAGE_SIZE` (20)
  per request. Add a tighter cap if the tool fans out per-item.
- Return `returned` and `has_more` alongside results.
- Include a source `url` on every returned item — `SERVER_INSTRUCTIONS` tells
  the model to cite from those fields. A null url should be explicit, not
  invented.
- Reuse `_book_summary` / `_work_summary` / `_node_summary` for result shaping
  so tools stay chainable (one tool's `book_id` feeds the next).
- Never hardcode the GraphQL key or endpoint.

Tests: add offline tests with fixtures to `tests/`, and a live test to
`tests/e2e/test_smoke_live.py`. CI enforces 55% coverage. New tests change the
collected count, so update the offline-tests row in `docs/quality.md` to match.

Finally, update every copy of the tool registry: add the tool to the table in
`README.md` and the docstring list at the top of `server.py`, bump the
`@mcp.tool` count in `AGENTS.md`, and bump the `_paginated_graphql_edges`
call-site count in `docs/reflections/2026-09-verification.md`.
