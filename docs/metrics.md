# PR metrics

What this project measures about its own change flow, why, and how to
recompute it. Numbers below are a snapshot — the commands are the durable part.

**Snapshot date:** 2026-09-16 (PRs #31–#40, the ACMM scaffolding series)

## PR acceptance

| metric | value |
|---|---|
| PRs merged | 10 |
| PRs closed without merging | 0 |
| acceptance rate | 10/10 |
| median time open | ~35 min |

```bash
gh pr list --state merged --limit 100 --json number --jq 'length'
gh pr list --state closed --limit 100 --json number,mergedAt \
  --jq '[.[] | select(.mergedAt == null)] | length'
```

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
gh api repos/{owner}/{repo}/pulls/<N>/comments \
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
| coverage gate | 55% (`--cov-fail-under`), actual ~61% |

```bash
gh run list --workflow ci.yml --limit 30 --json conclusion \
  --jq 'group_by(.conclusion)[] | "\(.[0].conclusion): \(length)"'
pytest -q --cov=goodreads_mcp --cov-report=term-missing
```

## What is deliberately not measured

- **Velocity / PR throughput.** This is a single-maintainer hobby project;
  optimizing throughput would mean rushing review, which is the opposite of
  what the gates exist for.
- **Coverage as a target.** The gate is a floor against regression, not a
  number to climb. Chasing it would mean testing the fixture-backed paths that
  are already covered, when the uncovered risk is live-endpoint drift that
  offline tests cannot reach at any coverage percentage.
