[![CI](https://github.com/Danathar/goodreads-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Danathar/goodreads-mcp/actions/workflows/ci.yml)
[![Nightly compliance](https://github.com/Danathar/goodreads-mcp/actions/workflows/nightly-compliance.yml/badge.svg?branch=main)](https://github.com/Danathar/goodreads-mcp/actions/workflows/nightly-compliance.yml)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Danathar/goodreads-mcp)
[![Maintenance assisted by Hivecommons Hive](https://img.shields.io/badge/maintenance%20assisted%20by-Hivecommons%20Hive-1f6feb)](https://github.com/hivecommons/hive)
[![ACMM L5 Semi-Autonomous](https://img.shields.io/badge/ACMM-L5%20Semi--Autonomous-2da44e)](docs/maintenance.md)
[![AI assisted](https://img.shields.io/badge/AI-assisted-d29922)](#about-this-project)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

# 📚 goodreads-mcp

A **read-only** MCP server for Goodreads — built without the Goodreads API, because there hasn't been one since December 2020. Lets an LLM find and research books, ratings, and reviews. Tools ride on RSS feeds, the JSON autocomplete endpoint, the `__NEXT_DATA__` blob embedded in book pages, and the AppSync GraphQL backend the Goodreads website itself uses. No login, no cookies, no writes — public data only.

<!-- Ownership token the official MCP registry checks in the PyPI README
     before it accepts a listing; must match `name` in server.json. Keep it
     on its own line. mcp-name: io.github.Danathar/goodreads-mcp -->

## tools

| tool | source / what it returns |
|---|---|
| `search_books` | JSON autocomplete endpoint (stable) — book_id, title, author, rating, cover; `max_results` defaults to 10, but the endpoint returns about 5 matches at most |
| `get_book` | `__NEXT_DATA__` via the `.xml` page (stable) — details, cover, ratings histogram, every series membership, review-language breakdown (`review_language_limit`, default 5, max 25) |
| `get_reviews` | GraphQL — paginated reader reviews (text, rating, likes, date, spoiler flag, permalink); `limit` default 10, capped at 100; server-side `min_rating` / `max_rating` (1–5, min ≤ max) and `exclude_spoilers`; reports `has_more` |
| `similar_books` | GraphQL — paginated "readers also enjoyed" recommendations; `limit` up to 100 |
| `author_books` | GraphQL — paginated author bibliography, ranked by popularity, from any of their books, plus `author_url`; `limit` up to 100 |
| `series_books` | GraphQL — paginated series books with reading-order placement; `series_index` (zero-based, in `get_book`'s `series_memberships` order) picks the series; `limit` up to 100 |
| `get_editions` | GraphQL — paginated editions (format, ISBN, publisher, date); `limit` up to 100 |
| `book_lists` | GraphQL — paginated Listopia lists a book appears on (title, votes, size); `limit` up to 100 |
| `popular_books` | GraphQL — most popular books by release `year`, or a single `month` (1–12), ranked; `limit` capped at 50 |
| `compare_books` | `get_book` for each id (`__NEXT_DATA__` via `.xml`) — ranks 1–10 books by rating with positive/critical share; more than 10 ids is refused; a book that fails comes back as an `error` entry |
| `get_shelf` | shelf RSS feed (stable) — books on a public shelf; `page` starts at 1, about 100 items per page; `user_id` overrides the configured user |
| `list_shelves` | best-effort HTML scrape of the public profile page — shelf names; raises `LoginRequired` for a private profile |

Every tool is registered with MCP read-only annotations (read-only, non-destructive, idempotent).

The discovery tools all take a `book_id` and return results carrying `book_id`/title/author/rating/url, so an agent can chain them — e.g. `similar_books` → `get_reviews` on a recommendation. This is the structured book graph a general web search can't assemble.

The five paginated discovery tools (`similar_books`, `author_books`, `series_books`, `get_editions`, `book_lists`) page in batches of 20 and accept a total `limit` up to 100; `popular_books` caps `limit` at 50. Responses include `returned` and `has_more`, keeping larger lookups useful without allowing unbounded traffic.

> **WAF and login note:** Goodreads book HTML pages now sit behind an AWS WAF
> JavaScript challenge (HTTP 202) that plain HTTP clients can't solve. `get_book`
> routes around it via the `.xml`-suffixed page, so it still works without a
> browser. If Goodreads ever extends the WAF to a path we depend on, the client
> raises `WAFChallenge` with a clear message instead of a confusing parse error.
> The review-list page (`/review/list/{uid}`) became login-only in Sep 2026; the
> client raises `LoginRequired` on a sign-in redirect for the same reason, and
> `list_shelves` reads the public profile page instead.

## install

With pip:

```bash
cd goodreads-mcp
python3.10 -m venv .venv && .venv/bin/pip install -e .
```

Or with [uv](https://docs.astral.sh/uv/), which is also what the Claude Desktop bundle uses:

```bash
cd goodreads-mcp
uv sync
uv run goodreads-mcp
```

Requires Python ≥ 3.10.

Each date-numbered release is also published to PyPI as [`goodreads-mcp-ai`](https://pypi.org/project/goodreads-mcp-ai/) (the `goodreads-mcp` name there belongs to an unrelated project) and listed on the [official MCP registry](https://registry.modelcontextprotocol.io) as `io.github.Danathar/goodreads-mcp`, from [`server.json`](server.json). A client that installs from the registry runs `uvx goodreads-mcp-ai`.

## config (optional)

No login or cookies — everything is public data. The only setting is your numeric `user_id`, the default for the shelf tools. It's the number in `goodreads.com/user/show/<ID>-yourname`; you can also pass `user_id` to each shelf tool per call.

```bash
mkdir -p ~/.config/goodreads-mcp
cat > ~/.config/goodreads-mcp/config.json << 'EOF'
{ "user_id": "12345678" }
EOF
```

Env var `GOODREADS_USER_ID` overrides the file. A config file that can't be read, isn't valid JSON, isn't a JSON object, or has a non-string `user_id` is ignored with a warning on stderr; the server still starts.

## Claude Desktop config

**Bundle.** Each [release](https://github.com/Danathar/goodreads-mcp/releases) carries a `goodreads-mcp.mcpb`. Releases come out monthly when the server itself changed, numbered by date (`2026.10.0`, `2026.10.1`, `2026.11.0`); `0.1.1` was the last of the old numbering, and every date-numbered release is newer than it. See [CONTRIBUTING.md](CONTRIBUTING.md#releases) for how one is cut. Open the `.mcpb` in Claude Desktop to install. The bundle ships no dependencies — the manifest launches the server with `uv run`, and the host resolves `pyproject.toml` into a private environment on first launch — so one bundle runs on macOS, Windows and Linux with any Python ≥ 3.10. The bundle's optional "Goodreads User ID" setting (`user_config.goodreads_user_id`) is passed to the server as `GOODREADS_USER_ID`.

**Manual.** Add the server to `claude_desktop_config.json` — on macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, on Windows `%APPDATA%\Claude\claude_desktop_config.json`; in any version, Settings → Developer → Edit Config opens it:

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

For an end-to-end example that chains the tools, see [prompts/research-a-book.md](prompts/research-a-book.md).

## tests

```bash
.venv/bin/pip install -e ".[test]"     # pytest + pytest-cov
.venv/bin/pytest                       # offline parser/unit tests
GOODREADS_LIVE=1 .venv/bin/pytest      # + live network smoke tests
```

The offline suite runs on fixtures; CI runs it with `pytest-cov` and enforces a coverage floor (`--cov-fail-under` in [ci.yml](.github/workflows/ci.yml)). The live smoke tests are in [tests/e2e/test_smoke_live.py](tests/e2e/test_smoke_live.py) and skip unless `GOODREADS_LIVE=1` is set. The [nightly compliance run](.github/workflows/nightly-compliance.yml) runs the live suite against the real endpoints every night, so upstream drift shows up within a day.

## documentation

- [docs/design.md](docs/design.md) — design notes: the data surfaces, WAF and login handling, politeness and concurrency
- [docs/roadmap.md](docs/roadmap.md) — ideas not built yet
- [docs/maintenance.md](docs/maintenance.md) — how this repository is maintained (Hive, ACMM L5, human review)
- [docs/quality.md](docs/quality.md) — what the tests and numbers do and do not prove
- [docs/risk-tiers.md](docs/risk-tiers.md) — the risk tier every pull request declares
- [docs/review-rubric.md](docs/review-rubric.md) — the review checklist
- [docs/metrics.md](docs/metrics.md) — outcome metrics
- [docs/reflections/](docs/reflections/) — lessons learned about this codebase
- [docs/SECURITY-AI.md](docs/SECURITY-AI.md) — what AI agents may and may not touch

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
