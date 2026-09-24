# PR metrics

What this project measures about its own change flow, why, and how to
recompute it. Numbers below are a snapshot — the commands are the durable part.

Every command names `Danathar/goodreads-mcp`. This repository is a fork, and in
a clone that has the parent as an `upstream` remote, a bare `gh` reads the
parent: the first command below printed 0 instead of 10 that way.

**Snapshot date:** 2026-09-16 (PRs #31–#40, the ACMM scaffolding series)

Later readings of the same numbers over the whole history, each dated and left
as it was read, are in [`docs/metrics/`](metrics/2026-09-24.md).

## PR acceptance

| metric | value |
|---|---|
| PRs merged | 10 |
| PRs closed without merging | 0 |
| acceptance rate | 10/10 |
| median time open | ~35 min |

```bash
# scoped to the snapshot range so these stay reproducible as new PRs land
gh pr list --repo Danathar/goodreads-mcp --state merged --limit 100 --json number \
  --jq '[.[] | select(.number >= 31 and .number <= 40)] | length'
gh pr list --repo Danathar/goodreads-mcp --state closed --limit 100 --json number,mergedAt \
  --jq '[.[] | select(.number >= 31 and .number <= 40 and .mergedAt == null)] | length'
```

Note on numbering: GitHub shares one number space between issues and PRs, and
this repo's issues occupy #1–#30. Its first PR is therefore #31 — there is no
PR below that number. Drop the range filter and you get the all-time figure,
which is the right query for a current snapshot but won't reproduce the table
above once later PRs merge.

**Read this one carefully.** A 100% acceptance rate is not a quality signal
here — it reflects a single author merging their own scaffolding PRs over two
sessions. Acceptance rate only becomes meaningful with independent reviewers
who can decline. Treat the current value as a baseline to watch, not as
evidence of anything.

## Review findings

Automated review (Codex) runs on every PR. Across #31–#40:

| | |
|---|---|
| PRs reviewed | 10 |
| PRs with at least one finding | 5 |
| total findings | 9 |
| findings resulting in a code change | 5 |
| findings resolved as not-applicable | 4 |

```bash
# inline findings for one PR
gh api repos/Danathar/goodreads-mcp/pulls/<N>/comments \
  --jq '[.[] | select(.user.login | contains("codex"))] | length'
```

Caveat on the method: findings arrive in two shapes — inline review comments
and items embedded in a review body. The command above counts only the first,
and undercounts PRs like #31 where both findings were in the body. Count both
before quoting a total.

The not-applicable half is worth understanding rather than optimizing away.
Four findings were correct observations about a branch reviewed in isolation —
links to `AGENTS.md` before the PR adding it had merged. They were real, and
the right resolution was merge ordering, not a code change.

## CI

| metric | value |
|---|---|
| last 30 `ci.yml` runs | 28 success, 1 non-success, 1 in flight |
| coverage gate | 55% (`--cov-fail-under`) |
| coverage actual | 100% at `9cfcf92`, 2026-09-20 |

```bash
gh run list --repo Danathar/goodreads-mcp --workflow ci.yml --limit 30 --json conclusion \
  --jq 'group_by(.conclusion)[] | "\(.[0].conclusion): \(length)"'
pytest -q --cov=goodreads_mcp --cov-report=term-missing
```

The two coverage rows age differently, which is why they are now separate rows.
The **gate** is a constant: it lives in `.github/workflows/ci.yml` as
`--cov-fail-under`, is mirrored in
[`.coverage-thresholds.json`](../.coverage-thresholds.json) and
[`.github/auto-qa-tuning.json`](../.github/auto-qa-tuning.json), and quoted by
every doc that mentions the number; `tests/test_coverage_thresholds.py` fails
if any copy disagrees with the workflow.

The **actual** figure is a reading of HEAD, and unlike the PR-acceptance rows
above — which are scoped to PRs #31–#40 precisely so they stay reproducible —
it goes stale every time a test lands. It did: it was recorded as ~61% and sat
there while four test PRs took it to 82% ([#63](https://github.com/Danathar/goodreads-mcp/issues/63)).
Re-read it with the command above and update the commit and date; no test can
pin it without running coverage inside the suite that measures it.

## What is deliberately not measured

- **Velocity / PR throughput.** This is a single-maintainer hobby project;
  optimizing throughput would mean rushing review, which is the opposite of
  what the gates exist for.
- **Coverage as a target.** The gate is a floor against regression, not a
  number to climb. Chasing it would mean testing the fixture-backed paths that
  are already covered, when the uncovered risk is live-endpoint drift that
  offline tests cannot reach at any coverage percentage.
