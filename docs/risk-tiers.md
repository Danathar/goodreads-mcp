# Change risk tiers

How to classify a change to this repo, and what each tier requires before
merging. Used by reviewers and agents; complements the
[review rubric](review-rubric.md), which is the *how*, where this is the
*how much*.

Tier is set by **what the change can break**, not by diff size. A one-line
change to a parser is Tier 1; a 300-line docs PR is Tier 3.

## Tier 1 — can break users silently

Changes to how data is fetched or parsed. This is the dangerous tier, because
the failure is invisible to CI: fixtures keep passing while the live site
returns something else.

Covers: `client.py` request construction, retry, or WAF detection; any parser
(`__NEXT_DATA__`/Apollo traversal, RSS, the `list_shelves` HTML regex); GraphQL
queries or the config-discovery regexes.

**Required:**
- `GOODREADS_LIVE=1 pytest tests/e2e -v` run and passing — **a green offline
  suite is not evidence here**
- An offline fixture test covering the new shape, so the regression is caught
  next time
- Review by someone who can reason about the affected surface

## Tier 2 — can break the build or the contract

Changes to tool signatures, return shapes, pagination, caps, CI, packaging, or
permissions.

Covers: new or changed `@mcp.tool` functions; `_MAX_*` / `_PAGE_SIZE` caps;
`pyproject.toml` / `manifest.json`; `.github/workflows/**`; `.github/policies/**`;
`.claude/settings.json`; `.claude/hooks/**`.

**Required:**
- `pytest -q` passing, coverage gate satisfied
- Return-shape changes noted in `README.md` and the `server.py` docstring list
- Version bumps applied to `pyproject.toml` **and** `manifest.json` together
- Workflow changes: state what was verified and what couldn't be (a cron
  schedule can't be proven before it fires)
- `.claude/settings.json` or `.claude/hooks/**`: a human reads the diff and
  merges it; a green suite is not enough on its own

### Why the agent boundary needs a human

`.claude/settings.json` is the permission table and `.claude/hooks/guard-bash.py`
is the `PreToolUse` guard that keeps the allow-listed verbs from reaching past
`Read(./.env)`. Together they are the boundary an agent works inside. A wrong
change there does not break the build. It gives an agent unprompted reach, and
CI stays green, because the guard's tests are its own tables in
`tests/test_agent_permissions.py`: one pull request can relax a refusal and drop
the row that pinned it. That is the silent failure Tier 1 exists for, so a
passing suite does not count as review here. Every way past the guard found so
far (#60, #71, #115, #120) was a one-line difference.

## Tier 3 — contained

Docs, comments, agent instruction files, prompts, skills, labels. The
`.claude/` files that run are not instruction files: the permission table and
the hooks are Tier 2 (see above).

**Required:**
- Claims about the code checked against the code. Most findings on this repo's
  docs PRs have been documentation asserting something the source contradicts —
  an over-broad "never parse the DOM" rule, a pagination helper described as
  universal, a metrics command that didn't reproduce its own table.

## Out of scope, any tier

Not a risk tier — a scope boundary. Decline rather than classify:

- Anything requiring auth, cookies, or credentials
- Any write operation against Goodreads
- Anything raising request rate against these unofficial endpoints
- Hardcoding the GraphQL key or endpoint

## Applying this

There's no automated classifier. It's a judgment call made in the PR
description: say which tier and why, so a reviewer can disagree. The
[labeler](../.github/labeler.yml) applies path labels (`client`, `server`,
`live-tests`, `ci`) that correlate with tier but don't determine it — a `docs`
label on a PR that also edits a parser doesn't make it Tier 3.
