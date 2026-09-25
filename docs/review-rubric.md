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
- [ ] No new concurrency that multiplies request rate. Tool calls already run
      in worker threads; `client.MAX_IN_FLIGHT` caps what reaches the wire,
      and a change to it needs a reason in the PR.
- [ ] A new **standard discovery connection** uses `_paginated_graphql_edges`
      and respects the caps (`_MAX_DISCOVERY`, `_DISCOVERY_PAGE_SIZE`).
      The helper only handles Goodreads' standard `PaginationInput`/`PageInfo`
      shape — don't demand it where it doesn't fit. Two tools legitimately
      paginate by hand and should stay that way:
      `get_reviews` (client-side spoiler filtering changes the returned count,
      so it needs its own loop, with its own `_MAX_REVIEWS` / `_MAX_REVIEW_PAGES`
      caps) and `popular_books` (passes `after` and `limit`
      as top-level variables, with its own `_MAX_POPULAR` / `_POPULAR_PAGE_SIZE`
      caps). `compare_books` likewise has its own `_MAX_COMPARE` fan-out cap.

## 4. Tool contract

- [ ] `@mcp.tool(annotations=_READ_ONLY)` on new tools.
- [ ] Results carry a source `url` so the model can cite them; null is explicit,
      never fabricated.
- [ ] `returned` / `has_more` present on results paged through
      `_paginated_graphql_edges` and on the hand-paginated `popular_books` and
      `get_reviews`.
- [ ] Results shaped so tools chain — one tool's `book_id` feeds the next.

## 5. Tests

- [ ] Offline fixture test covering the new shape, so the next regression is
      caught by `pytest -q`.
- [ ] Live test added for a new tool.
- [ ] Coverage gate (55%) still passes.

## 6. Housekeeping

- [ ] `pyproject.toml` and `manifest.json` versions in sync — release CI fails
      if they drift.
- [ ] The version changes only in a release pull request, opened by the
      `prepare` run of the release workflow. Versions are CalVer
      (`YYYY.M.PATCH`, no "v" prefix). Release CI refuses a version that is
      not CalVer, is already tagged, or is not newer than the last release.
- [ ] New tool added to the `README.md` table and the `server.py` docstring list,
      and the `@mcp.tool` count in `AGENTS.md` and the `_paginated_graphql_edges`
      call-site count in `docs/reflections/2026-09-verification.md` bumped.
- [ ] No generated files committed (`.coverage`, `uv.lock`, `.venv/`, `*.mcpb`).
- [ ] A new runtime dependency goes in `pyproject.toml` only. The bundle ships
      none: `manifest.json` launches with `uv run`, which resolves that file on
      the user's machine. Nothing may put packages or compiled modules in the
      tree `mcpb pack` reads — release CI fails on a compiled module while the
      manifest lists more than one platform (#89).

## Automated review

Codex reviews every PR. Its findings are advisory, not authoritative — verify
before acting. In this repo's history it has been right about real problems
(a committed `.coverage` file carrying an absolute local path; an
over-broad "never parse the DOM" rule that contradicted `list_shelves`) and
also right-but-not-actionable (flagging links to a file that a not-yet-merged
PR adds). Both outcomes are normal. Reply with the reasoning and resolve the
thread either way.
