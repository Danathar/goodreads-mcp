# Strategy

This page says what `goodreads-mcp` is trying to be, what it will not do, and
how to tell whether it is on track. It holds no numbers that change. Every
signal below is a command or a file you run or open yourself, so the answer
is always today's.

Every `gh` command names `Danathar/goodreads-mcp`. This repository is a fork,
and in a clone that has the parent as an `upstream` remote, a bare `gh` reads
the parent instead (see [`metrics.md`](metrics.md)).

## What this project is trying to be

A read-only MCP server that lets an LLM find and research books, ratings and
reviews on Goodreads, which has had no public API since December 2020
([README](../README.md), [AGENTS.md](../AGENTS.md)). It reaches Goodreads
through unofficial read surfaces, so most of the work is keeping those
surfaces working and failing loudly when they move
([design notes](design.md)).

Three things follow from that:

1. **It keeps working.** The threat is upstream drift: a changed page shape, a
   rotated GraphQL key, a WAF extended to one more path. Offline tests run on
   fixtures and cannot see it, so the nightly live run does
   ([`maintenance.md`](maintenance.md)).
2. **It fails loudly.** `WAFChallenge`, `LoginRequired` and `GraphQLError` are
   raised instead of feeding bad markup to a parser.
3. **A person reads every change.** Agents may propose; a human merges
   ([`maintenance.md`](maintenance.md), [risk tiers](risk-tiers.md)).

## What it deliberately will not do

From the hard constraints in [AGENTS.md](../AGENTS.md) and
[CONTRIBUTING.md](../CONTRIBUTING.md):

- **No writes and no credentials.** No login, no cookies. A change that adds
  either is out of scope.
- **No extra load on Goodreads.** One shared client, backoff on 429/503, and
  `client.MAX_IN_FLIGHT` as the cap on parallel requests. Raising it needs a
  reason.
- **No browser automation.** Everything is plain HTTP requests
  ([design notes](design.md)).
- **No hardcoded GraphQL key or endpoint.** Both are discovered at runtime.
- **No new HTML scraping where a structured surface exists.** `list_shelves`
  is the one explicit exception.

From [`metrics.md`](metrics.md), what it deliberately does not measure:

- **Velocity or PR throughput.** Optimizing it would mean rushing review.
- **Coverage as a target.** The coverage gate is a floor against regression,
  not a number to climb.

## What is not decided

[`roadmap.md`](roadmap.md) lists ideas that are not built: author page detail
and a caching layer. Whether and when to build either is a maintainer
decision, and this page does not make it. The same goes for raising the
coverage floor: [`.github/auto-qa-tuning.json`](../.github/auto-qa-tuning.json)
records when a raise is warranted, and nothing applies it automatically.

## Signals

Each signal gives the question it answers, then the read. "Now" means the
answer changes as the repository does. "Pinned" means the query is scoped so
a later rerun gives the same answer.

### 1. Does it still work against live Goodreads? (now)

The nightly compliance run is the project's only live check
([`nightly-compliance.yml`](../.github/workflows/nightly-compliance.yml)). It
runs at 07:00 UTC, retries once, and on a scheduled failure opens or updates
one drift issue.

```bash
# the last 7 scheduled runs
gh run list --repo Danathar/goodreads-mcp --workflow nightly-compliance.yml \
  --event schedule --limit 7 --json createdAt,status,conclusion \
  --jq '.[] | "\(.createdAt) \(.status) \(.conclusion)"'

# is a drift issue open? (prints [] when none is)
gh issue list --repo Danathar/goodreads-mcp --state open \
  --search 'Nightly live check failing in:title' --json number,title
```

On track: recent runs are `completed success` and no drift issue is open.
A run with `status` other than `completed` is still in flight and says
nothing yet. One failure is not proof of drift, since the run already retries
once; several in a row are.

### 2. Is the offline gate green on `main`? (now)

```bash
gh run list --repo Danathar/goodreads-mcp --workflow ci.yml --branch main \
  --limit 5 --json createdAt,conclusion --jq '.[] | "\(.createdAt) \(.conclusion)"'
```

### 3. What work is open? (now)

```bash
# open issues, counted by label (issues with no label do not appear)
gh issue list --repo Danathar/goodreads-mcp --state open --limit 200 --json labels \
  --jq '[.[].labels[].name] | group_by(.) | map("\(.[0]): \(length)")'

# open issues carrying one label, e.g. bugs
gh issue list --repo Danathar/goodreads-mcp --state open --label bug --json number,title

# open pull requests held for a human batch review
gh pr list --repo Danathar/goodreads-mcp --state open --label hold --json number,title
```

The `--limit 200` is a ceiling, not a window: if the repository ever has more
open issues than that, the count is short. An empty result means nothing is
open under that label.

### 4. How does change flow? (pinned, or now when you drop the range)

[`metrics.md`](metrics.md) defines the measurements: PR acceptance, review
findings and CI outcomes, and says why acceptance rate is not a quality
signal while one maintainer merges everything. Its commands are the
reads. [`metrics/2026-09-24.md`](metrics/2026-09-24.md) is a dated reading over
the whole history, left as it was read.

Two things to know before running them:

- The range filters (`.number >= 31 and .number <= 40`) pin a query. The
  `--limit` does not: `gh pr list` returns only the newest `--limit` PRs, so a
  limit smaller than the repository's PR count silently drops the old ones.
  The `metrics.md` commands use `--limit 1000` for that reason.
- Findings arrive as inline comments and as review bodies. The inline-only
  command undercounts; `metrics/2026-09-24.md` counts both.

### 5. Is it released on schedule? (now)

Releases are monthly and CalVer (`YYYY.M.PATCH`), run by
[`release.yml`](../.github/workflows/release.yml) at 09:00 UTC on the 1st, as
described in [CONTRIBUTING.md](../CONTRIBUTING.md#releases). The scheduled run
does nothing unless the repository variable `AUTO_RELEASE_ENABLED` is `true`,
and it skips a month in which nothing under `goodreads_mcp/` changed. A month
with no release is therefore not by itself a failure.

```bash
# newest releases
gh release list --repo Danathar/goodreads-mcp --limit 5

# the last scheduled release runs and how they ended
gh run list --repo Danathar/goodreads-mcp --workflow release.yml --event schedule \
  --limit 5 --json createdAt,conclusion --jq '.[] | "\(.createdAt) \(.conclusion)"'

# is automatic release switched on? (needs read access to repository variables)
gh variable list --repo Danathar/goodreads-mcp
```

A scheduled run that ended `failure` is worth opening: the run log says which
refusal condition from CONTRIBUTING.md it hit, and some of them (version not
newer than the last release, no passing `test` check) are expected stops
rather than faults.

### 6. Is the coverage floor intact? (the floor is constant; the reading is not)

The floor is the number CI enforces. It is written in the workflow and
mirrored in two JSON files, and `tests/test_coverage_thresholds.py` fails if
the copies disagree.

```bash
grep -o -e '--cov-fail-under=[0-9]*' .github/workflows/ci.yml
jq .min_line_coverage_percent .coverage-thresholds.json
jq .coverage.floor_percent .github/auto-qa-tuning.json
```

The measured figure is a reading of `HEAD`, not a constant. The
`measured_percent` fields in those files are dated and go stale; take a fresh
reading instead:

```bash
pytest -q --cov=goodreads_mcp --cov-report=term-missing
```

On track: that run passes and its total stays above the floor with room to
spare. The floor guards against regression. It is not a target
([`metrics.md`](metrics.md)).

## How this page is kept honest

Nothing here quotes a count, a date or a percentage that a later commit can
make false. When you change a command, run it, and check that it prints what
the text above says it prints. When you add a signal, give the command and
say whether it is a now-reading or pinned.
