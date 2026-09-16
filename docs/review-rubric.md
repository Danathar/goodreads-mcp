# PR review rubric

How changes to this repo get reviewed. Written for both human reviewers and
agents. Repo conventions: [AGENTS.md](../AGENTS.md).

Review in this order — the first section that fails is the one to fix.

## 1. Scope

- [ ] Does it stay read-only? No auth, no cookies, no writes, no credentials.
      This is the project's defining constraint; a PR that breaks it should be
      declined rather than tidied up.
- [ ] Is the change the size of the problem? A parser fix shouldn't refactor
      the client.

## 2. Correctness against the real endpoints

This is where this project actually breaks, so it gets the most weight.

- [ ] If parsing changed, was `GOODREADS_LIVE=1 pytest tests/e2e -v` run?
      **A green offline suite is not evidence** — it runs on fixtures and
      cannot see a Goodreads markup change.
- [ ] Book pages fetched via the `.xml` path, not plain HTML? (The plain page
      is WAF-gated and returns HTTP 202, which `raise_for_status()` won't catch.)
- [ ] `__NEXT_DATA__` / Apollo state parsed rather than markup — except
      `list_shelves`, which scrapes HTML because shelf names have no
      structured surface.
- [ ] No hardcoded GraphQL key or endpoint. Both are resolved at runtime so
      rotations self-heal.
- [ ] Book ids handled in slug form (`11870085-the-fault-in-our-stars`), not
      assumed numeric.

## 3. Politeness to an unofficial API

- [ ] Single shared client, backoff on 429/503, browser-faithful headers intact.
- [ ] No new concurrency that multiplies request rate.
- [ ] New pagination uses `_paginated_graphql_edges` and respects the caps
      (`_MAX_DISCOVERY`, `_DISCOVERY_PAGE_SIZE`, and the tighter per-tool ones).

## 4. Tool contract

- [ ] `@mcp.tool(annotations=_READ_ONLY)` on new tools.
- [ ] Results carry a source `url` so the model can cite them; null is explicit,
      never fabricated.
- [ ] `returned` / `has_more` present on paginated results.
- [ ] Results shaped so tools chain — one tool's `book_id` feeds the next.

## 5. Tests

- [ ] Offline fixture test covering the new shape, so the next regression is
      caught by `pytest -q`.
- [ ] Live test added for a new tool.
- [ ] Coverage gate (55%) still passes.

## 6. Housekeeping

- [ ] `pyproject.toml` and `manifest.json` versions in sync — release CI fails
      if they drift.
- [ ] New tool added to the `README.md` table and the `server.py` docstring list.
- [ ] No generated files committed (`.coverage`, `vendor/`, `*.mcpb`).

## Automated review

Codex reviews every PR. Its findings are advisory, not authoritative — verify
before acting. In this repo's history it has been right about real problems
(a committed `.coverage` file carrying an absolute local path; an
over-broad "never parse the DOM" rule that contradicted `list_shelves`) and
also right-but-not-actionable (flagging links to a file that a not-yet-merged
PR adds). Both outcomes are normal. Reply with the reasoning and resolve the
thread either way.
