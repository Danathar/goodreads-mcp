# Corrections

Append-only log. See [README.md](README.md) for the format.

---

## Book HTML pages are WAF-gated; use the .xml path
**Date:** 2026-09-16
**Wrong:** Fetching `goodreads.com/book/show/<id>` and parsing the response.
**Right:** That path returns an AWS WAF JS challenge as **HTTP 202** with a
body only a real browser can solve. `get_book` uses the `.xml`-suffixed page
instead. The client detects challenge bodies and raises `WAFChallenge`.
**Why it matters:** 202 is a success status, so `raise_for_status()` does not
catch it. Without explicit detection, challenge markup reaches the parser and
surfaces as a confusing parse error rather than "this path is blocked."

## Never hardcode the GraphQL key or endpoint
**Date:** 2026-09-16
**Wrong:** Pasting the observed `da2-...` key or the `appsync-api` URL into the
source as a constant.
**Right:** `client.graphql_config` resolves the key from page-level
`__NEXT_DATA__` and the endpoint from the `_app` JS bundle at runtime, caching
per process. A 401/403 forces one re-discovery and retry.
**Why it matters:** Goodreads rotates both. Runtime resolution self-heals;
a hardcoded value turns a rotation into an outage.

## Offline tests cannot catch a Goodreads change
**Date:** 2026-09-16
**Wrong:** Treating a green `pytest -q` as evidence that a parsing change works.
**Right:** The offline suite runs on fixtures. Run
`GOODREADS_LIVE=1 pytest tests/e2e -v` after any parsing change.
**Why it matters:** Upstream markup changes are this project's most common
real failure, and they are invisible to fixture-based tests by construction.

## A command-string guard must read what the shell runs, not what it parses
**Date:** 2026-09-18
**Wrong:** Tokenising the Bash command with a default `shlex.shlex` and
comparing the tokens against a table of denied flags.
**Right:** `shlex` defaults to `commenters = '#'`, so it ended the command at
the first `#` in any position, while a shell starts a comment only at the start
of a word — `pytest --ignore=z#z /tmp/evil.py` reached the guard as
`pytest --ignore=z`. And brace expansion runs after the hook has decided, so
`--no-inde{x,x}` is `--no-index` by the time git sees it. The lexer is given no
comment character, and braces and option-position globs are refused rather than
expanded.
**Why it matters:** The gap between the string a guard parses and the string
the shell executes is the whole gate. Seeing *more* than the shell runs costs a
false refusal; seeing *less* costs everything the guard was added for — here,
all of #60. See #71.

## Book ids may be slugs
**Date:** 2026-09-16
**Wrong:** Assuming `book_id` is numeric.
**Right:** Slug forms like `11870085-the-fault-in-our-stars` are valid inputs.
Use `_resolve_book_ids` / `_legacy_id` rather than casting to int.
**Why it matters:** Users paste URLs. A naive `int(book_id)` raises on input
the server is documented to accept.

## A followed redirect to the sign-in page is a 200
**Date:** 2026-09-20
**Wrong:** Reading a 200 (or a clean `raise_for_status()`) as "the page asked
for came back". `list_shelves` read the sign-in form, found no shelf links,
and returned `[]` for every user, public profiles included.
**Right:** The client follows redirects, so a login-gated path (the review-list
page `/review/list/{uid}` since Sep 2026, #91) lands on `/user/sign_in` as a
200 login form. `client._request` checks where the response landed
(`resp.url.path`) and raises `LoginRequired`; `list_shelves` reads the
still-public profile page `/user/show/{uid}` instead.
**Why it matters:** An empty result reads as "this user has no shelves", not
"Goodreads moved this behind a login". A surface can go login-only without any
status changing, so only the landing path tells.
