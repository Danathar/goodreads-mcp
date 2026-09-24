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

## Gitignored is not bundle-ignored
**Date:** 2026-09-21
**Wrong:** Reading `.gitignore` as the list of files that stay out of a
release. `.coverage` was gitignored, so it looked handled.
**Right:** `mcpb pack` reads the working tree. Only `.mcpbignore` decides what
a bundle carries, and it did not name `.coverage`, so any tree that had run the
`regenerate` command in `.coverage-thresholds.json` packed one — absolute paths
and all. Every `.gitignore` pattern now has to appear in `.mcpbignore`, checked
in `tests/test_stdio_launch.py`.
**Why it matters:** Two ignore files for two different consumers drift in
silence, and the one nobody reads is the one that publishes. See #109.

## A VAR=value in front of a command is part of the command
**Date:** 2026-09-22
**Wrong:** Treating a leading assignment as context around the command.
`guard-bash.py` popped `VAR=value` tokens off the front before it read
anything, and checked the names only when the verb was `pytest`.
**Right:** The shell evaluates the value and applies it to the process the
allow list started, so both halves are part of the command.
`GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD` ran `prog` once per changed path
with no prompt, and `FOO=$(...) pytest -q` substituted unseen because the value
was never scanned. Names now come from a safe list, for every guarded verb, and
a command substitution anywhere on a line that names a guarded verb is refused.
**Why it matters:** A deny list of dangerous variable names cannot be
completed. The one here held seven and missed `GIT_EXTERNAL_DIFF`,
`PYTHONWARNINGS` and `LD_PRELOAD`, each of which runs code in the process. See
#115.

## An assignment has more spellings than `NAME=value`
**Date:** 2026-09-22
**Wrong:** Reading only `NAME=value` in front of the verb. Three spellings put
the same variable in the same environment and none of them matched:
`NAME+=value`, which bash creates when the variable is unset; `export
NAME=value` earlier in the string, which bash applies to every command after
it; and `env NAME=value verb`, where the wrapper stands where the guard reads
the verb. `env -S '...'` hides the whole invocation inside one word.
**Right:** All four are refused, and all four were run against git in a
throwaway repository first — each executes the program `GIT_EXTERNAL_DIFF`
names, once per changed path, exactly as the plain form does.
**Why it matters:** A rule stated as "an assignment before the command" has to
match bash's grammar for one, not the one spelling that came to mind. The
export form is the one worth remembering: the command it arms carries no
assignment at all.

## The permission table and the guard are not ordinary Tier 2 changes
**Date:** 2026-09-23
**Wrong:** `docs/risk-tiers.md` filed `.claude/settings.json` under Tier 2,
which needs only a green suite, and did not name `.claude/hooks/guard-bash.py`
at all, so its closest wording was Tier 3's "agent instruction files".
**Right:** Both are listed in Tier 2 with their own requirement: a human reads
and merges the change. SECURITY-AI.md and CONTRIBUTING.md say the same.
**Why it matters:** The guard's tests are its own tables. A change that weakens
the guard can remove the test rows that pinned it, and CI stays green. A green
suite cannot be the bar for the file that decides what an agent can reach. See
#129.

## "A human merges everything" was a sentence, not a setting
**Date:** 2026-09-24
**Wrong:** The README said nothing reaches `main` without a person having read
it, and every gate here (hold label, rubric, risk tiers, coverage) assumed it.
`main` had no branch protection and no ruleset, so any token with
`contents: write` could push to it and start `release.yml`.
**Right:** `.github/rulesets/main.json` holds the ruleset, and
`docs/branch-protection.md` says how an admin applies it and how anyone checks
it is live (`branches/main .protected` prints `true`).
**Why it matters:** A promise about review is only as strong as whatever
refuses the push. Check the setting, not the prose. See #148.
