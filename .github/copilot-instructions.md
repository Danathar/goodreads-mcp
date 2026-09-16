# Copilot instructions

Full guidance lives in [AGENTS.md](../AGENTS.md). The essentials, inline:

- **Read-only MCP server** for public Goodreads data. No login, no cookies, no
  writes, no credentials. Don't suggest write operations — they're out of scope.
- **Goodreads has no public API** (since Dec 2020). Tools ride shelf RSS, the
  JSON autocomplete endpoint, the `__NEXT_DATA__` blob on Next.js pages, an
  AppSync GraphQL backend, and — for `list_shelves` only — a best-effort HTML
  scrape of `/review/list/{uid}`.
- **On Next.js book pages, parse `__NEXT_DATA__` / Apollo state, not the
  markup.** `list_shelves` is the deliberate exception: shelf *names* have no
  structured surface, so it regexes `shelf=` params out of the review-list
  page. Don't extend scraping to data reachable another way.
- **Book HTML pages are behind an AWS WAF JS challenge** returning HTTP 202.
  Fetch book pages via the `.xml`-suffixed path, as `get_book` does.
- **Never hardcode the GraphQL API key or endpoint.** They're resolved at
  runtime in `client.graphql_config` so rotations self-heal.
- **Don't increase request rate.** Single shared client, exponential backoff on
  429/503, browser-faithful headers.
- **Tests:** `pytest -q` is offline and fixture-based (CI runs it with a 55%
  coverage gate). `GOODREADS_LIVE=1 pytest tests/e2e -v` hits the network — run
  it whenever you change parsing.
- Keep `pyproject.toml` and `manifest.json` versions in sync.
