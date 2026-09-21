---
name: Feature request
about: A new tool, field, or capability
title: ""
labels: enhancement
---

**What data or capability is missing?**

**Where would it come from?**
Goodreads has had no public API since Dec 2020, so everything this server
returns rides on five unofficial read surfaces (`AGENTS.md` describes each
in full):

- **Shelf RSS** — public shelves, as structured XML
- **Search autocomplete** — the JSON endpoint behind the search box
- **Embedded page JSON** — the `__NEXT_DATA__` blob on book pages
- **AppSync GraphQL** — the discovery and review tools
- **Scraped HTML** — `list_shelves` only, and explicitly best-effort

Note which of these (or something else) the feature would use, if known.

**Read-only?**
This server is read-only by design — no login, no writes. Requests requiring
authentication or mutation are out of scope.
