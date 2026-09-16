---
name: Feature request
about: A new tool, field, or capability
title: ""
labels: enhancement
---

**What data or capability is missing?**

**Where would it come from?**
Goodreads exposes data via RSS, the JSON autocomplete endpoint, the
`__NEXT_DATA__` blob on book pages, and an internal GraphQL API. Note which
of these (or something else) the feature would use, if known.

**Read-only?**
This server is read-only by design — no login, no writes. Requests requiring
authentication or mutation are out of scope.
