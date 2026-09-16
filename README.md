[![CI](https://github.com/Danathar/goodreads-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Danathar/goodreads-mcp/actions/workflows/ci.yml)
[![Nightly compliance](https://github.com/Danathar/goodreads-mcp/actions/workflows/nightly-compliance.yml/badge.svg?branch=main)](https://github.com/Danathar/goodreads-mcp/actions/workflows/nightly-compliance.yml)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Danathar/goodreads-mcp)
[![Maintenance assisted by Hivecommons Hive](https://img.shields.io/badge/maintenance%20assisted%20by-Hivecommons%20Hive-1f6feb)](https://github.com/hivecommons/hive)
[![ACMM L4 Security-Aware](https://img.shields.io/badge/ACMM-L4%20Security--Aware-2da44e)](https://github.com/hivecommons/hive#acmm-levels)
[![AI assisted](https://img.shields.io/badge/AI-assisted-d29922)](#about-this-project)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

# 📚 goodreads-mcp

A **read-only** MCP server for Goodreads — built without the Goodreads API, because there hasn't been one since December 2020. Lets an LLM find and research books, ratings, and reviews. Tools ride on RSS feeds, the JSON autocomplete endpoint, and the `__NEXT_DATA__` blob embedded in book pages. No login, no cookies, no writes — public data only.

## tools

| tool | stability |
|---|---|
| `search_books` | stable (JSON endpoint) |
| `get_book` | stable (`__NEXT_DATA__` via `.xml` path) — details, cover, ratings histogram, all series memberships, review-language breakdown |
| `get_reviews` | GraphQL — paginated reader reviews (text, rating, likes, date, spoiler flag, permalink) with server-side `min_rating` / `max_rating` and `exclude_spoilers`; `limit` up to 100 |
| `similar_books` | GraphQL — paginated "readers also enjoyed" recommendations |
| `author_books` | GraphQL — paginated author bibliography (from any of their books) |
| `series_books` | GraphQL — paginated series books with reading-order placement; selectable membership for books in multiple series |
| `get_editions` | GraphQL — paginated editions (format, ISBN, publisher, date) |
| `book_lists` | GraphQL — paginated Listopia lists a book appears on (title, votes, size) |
| `popular_books` | GraphQL — most popular books by release year (or year+month), ranked |
| `compare_books` | takes several book ids, ranks them by rating with positive/critical share |
| `get_shelf` | stable (RSS) — public shelves |
| `list_shelves` | best effort (HTML) — public profiles |

The discovery tools all take a `book_id` and return results carrying `book_id`/title/author/rating/url, so an agent can chain them — e.g. `similar_books` → `get_reviews` on a recommendation. This is the structured book graph a general web search can't assemble.

GraphQL discovery tools page in batches of 20 and accept a total `limit` up to
100. Responses include `returned` and `has_more`, keeping larger lookups useful
without allowing unbounded traffic.

> **WAF note:** Goodreads book HTML pages now sit behind an AWS WAF JavaScript
> challenge (HTTP 202) that plain HTTP clients can't solve. `get_book` routes
> around it via the `.xml`-suffixed page, so it still works without a browser. If
> Goodreads ever extends the WAF to a path we depend on, the client raises
> `WAFChallenge` with a clear message instead of a confusing parse error.

## install

```bash
cd goodreads-mcp
python3.10 -m venv .venv && .venv/bin/pip install -e .
```

Requires Python ≥ 3.10.

## config (optional)

No login or cookies — everything is public data. The only setting is your numeric `user_id`, the default for the shelf tools. It's the number in `goodreads.com/user/show/<ID>-yourname`; you can also pass `user_id` to each shelf tool per call.

```bash
mkdir -p ~/.config/goodreads-mcp
cat > ~/.config/goodreads-mcp/config.json << 'EOF'
{ "user_id": "12345678" }
EOF
```

Env var `GOODREADS_USER_ID` overrides the file.

## Claude Desktop config

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "goodreads": {
      "command": "/path/to/goodreads-mcp/.venv/bin/goodreads-mcp"
    }
  }
}
```

Or for development, `mcp dev goodreads_mcp/server.py` gives you the Inspector UI to poke each tool.

## first-run verification

The endpoints are unofficial, so verify in this order:

1. `search_books("project hail mary")` — should just work
2. `get_book("54493401")` — confirms the `.xml`/WAF workaround; check the histogram is populated
3. `get_reviews("54493401")` — should return real review text
4. `get_shelf("to-read")` — checks your `user_id` + RSS
5. `list_shelves()` — best-effort shelf-name scrape

## tests

```bash
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest                       # offline parser/unit tests
GOODREADS_LIVE=1 .venv/bin/pytest      # + live network smoke tests
```

## design notes

- **Request-first, no browser automation.** Everything is `httpx` against JSON/RSS/embedded-JSON/GraphQL surfaces; the only HTML regex is in `list_shelves` and the GraphQL config discovery.
- **GraphQL backbone (reviews).** `get_reviews` calls Goodreads' AppSync GraphQL endpoint — the same backend the website uses. The web app injects a public read-only API key into page-level `__NEXT_DATA__` and keeps the production endpoint in its `_app` bundle; the client resolves both at runtime and caches them, so rotations self-heal (`client.graphql_config`). Legacy bundles that carry a paired key and endpoint are still supported. This is what enables real pagination (past the ~30 reviews a page embeds) and server-side rating filters. GraphQL partial-success is respected: a deleted review's sub-resource just comes back `null` rather than failing the call.
- **WAF-aware.** Book pages sit behind an AWS WAF JS challenge; `get_book` uses the `.xml` path that isn't gated, and the client raises `WAFChallenge` if it ever gets a challenge body so failures are loud, not silent. (The GraphQL endpoint is a separate AppSync host and isn't WAF-gated.)
- **Polite client.** Single persistent session, browser-faithful headers, exponential backoff on 429/503; `get_reviews` caps paging at 100 reviews.
- **Caveats**: all of this is unofficial and depends on markup/endpoints/keys that can drift.

## shipped since v0.1

- **richer book data** — `get_book` includes covers, the ratings histogram, every series membership, normalized publication dates, and configurable review-language depth; `series_books` can traverse any listed membership, and `get_reviews` returns paginated, filterable reader reviews.
- **author bibliography** — `author_books` returns an author's works (ranked by popularity) plus a link to their author page (`author_url`).
- **bounded discovery pagination** — similar books, bibliographies, series, editions, and Listopia memberships can return up to 100 results with `has_more` metadata.

## ideas for v2

- author page detail (bio, photo, follower count) — not currently exposed cleanly: the author page is legacy server-rendered HTML with no structured JSON, and there's no discoverable GraphQL contributor-detail query, so this would require brittle DOM scraping. `author_books` links to the page instead.
- caching layer for repeated lookups (the discovery tools each resolve the book first; a small TTL cache would cut duplicate GraphQL calls)

## how this repository is maintained

Maintenance here is assisted by [**Hive**](https://github.com/hivecommons/hive) — the agent-orchestration software from the [Hivecommons](https://github.com/hivecommons) project — which runs a fleet of AI agents against this repository at **ACMM level 4 (Security-Aware)**.

L4 is deliberately short of autonomy: all agents may file issues; the quality, security and CI agents may additionally open pull requests carrying a hold label; every other agent stays advisory. **A human reviews and merges everything** — nothing reaches `main` without a person having read it. [docs/SECURITY-AI.md](docs/SECURITY-AI.md) sets out what agents may and may not touch, and the prompt-injection surface that comes with parsing an unofficial third party's payloads.

That matters more than usual here. This server rides on endpoints Goodreads never documented and does not owe anyone stability — a `__NEXT_DATA__` shape change, a rotated GraphQL key, or the WAF extending to one more path breaks it silently. The offline suite runs on fixtures and by construction cannot see any of that, so the [nightly compliance run](.github/workflows/nightly-compliance.yml) exercises the live suite against the real endpoints and surfaces upstream drift within a day instead of at the next release.

Each pass is meant to leave the next one starting from a better position:

- **The gates ratchet.** A coverage floor is enforced on every pull request (`ci.yml`, via `--cov-fail-under`). [`.github/auto-qa-tuning.json`](.github/auto-qa-tuning.json) records the rule for when raising it is warranted — sustained headroom over a full release cycle — but nothing applies the raise automatically; that stays a human decision.
- **Risk is classified, not guessed.** Every pull request declares a [risk tier](docs/risk-tiers.md) in its description — a judgment call a reviewer can disagree with, not an automated verdict. The [labeler](.github/labeler.yml) applies path labels (`client`, `server`, `live-tests`, `ci`) that inform that call without determining it. Either way a change to the client or the GraphQL config discovery is held to a different standard than a change to a doc.
- **Lessons are written down where the next pass will read them.** [`docs/reflections/`](docs/reflections/) holds what a piece of work taught about this codebase, [docs/review-rubric.md](docs/review-rubric.md) is the review checklist, and [AGENTS.md](AGENTS.md) is the standing brief.
- **The measurement is of outcomes, not activity.** [`docs/metrics.md`](docs/metrics.md) tracks acceptance rate, time to merge and review rounds — not lines written or PRs opened.

[docs/quality.md](docs/quality.md) is honest about what the numbers do *not* prove — chiefly that no fixture-backed test can detect the failure mode that actually threatens this project.

Learn more: [Hive](https://github.com/hivecommons/hive) · [the full ACMM policy matrix](https://github.com/hivecommons/hive/blob/v4/src/docs/acmm-policy-matrix.md)

## about this project

> [!NOTE]
> Work on this fork is done with AI assistance and should be treated cautiously.
>
> This is a third-party tool. It is not an official Goodreads or Amazon product, is not sanctioned by either, and uses no official API — there hasn't been one since December 2020. "Goodreads" is a trademark of its owner and is used here only to say what this software talks to.
>
> It reads public data only: no login, no cookies, no writes. It is provided as-is, with no promise that the endpoints it depends on will keep working or that using it is consistent with Goodreads' terms. Keep request volume modest. The maintainer is not responsible for rate limiting, blocking, data loss, or other consequences of using this software.

## license

This fork is licensed under the [GNU General Public License v3.0](LICENSE), version 3 only (`GPL-3.0-only`) — no automatic upgrade to later versions.

It incorporates code from [`shreeyachand/goodreads-mcp`](https://github.com/shreeyachand/goodreads-mcp), Copyright (c) 2026 Shreeya Chand, released under the MIT License. That code remains under MIT; its licence text and copyright notice are preserved in [LICENSE.MIT](LICENSE.MIT) as the MIT licence requires. The combined work — upstream code together with this fork's changes — is distributed under GPL-3.0.
