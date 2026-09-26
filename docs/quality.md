# Quality

The state of quality assurance in this repo: what's enforced mechanically,
what's known-weak, and where the real risk sits.

**Last reviewed:** 2026-09-20

## What runs automatically

| gate | where | blocking |
|---|---|---|
| offline test suite | `ci.yml` on every push/PR to `main` | yes |
| coverage floor, 55% | `ci.yml` (`--cov-fail-under`) | yes |
| MCPB manifest validation | `ci.yml` | yes |
| version sync (`pyproject` vs `manifest`) | `release.yml` | yes, at release |
| bundle carries no compiled module (it declares three platforms) | `release.yml` | yes, at release |
| packed bundle starts under the manifest's own `uv run` command | `release.yml` | yes, at release |
| release refused unless the run is on `main`, the commit is on `main`, `test` passed on it, no check is red, and the version is CalVer, untagged and newer than the last release | `release.yml` | yes, at release |
| automated code review | Codex, every PR | advisory |
| offline tests on source edit | `.claude/settings.json` hook | advisory, local |

## Current numbers

| | |
|---|---|
| offline tests | 1088 passing, 24 skipped (live, opt-in) |
| coverage | 100% overall — `config.py` 100%, `client.py` 100%, `server.py` 100% |
| CI, last 30 runs | 28 success |

Recompute:

```bash
pytest -q --cov=goodreads_mcp --cov-report=term-missing
gh run list --workflow ci.yml --limit 30 --json conclusion \
  --jq 'group_by(.conclusion)[] | "\(.[0].conclusion): \(length)"'
```

`tests/test_coverage_thresholds.py` reads all three rows: the test count is
pinned to what pytest collects (so a PR that adds tests updates this row), the
coverage figure to [`.coverage-thresholds.json`](../.coverage-thresholds.json),
and the CI row to the fuller copy in [`metrics.md`](metrics.md#ci).

## The honest weak spot

**Coverage is not the risk here, and raising it would not reduce the risk.**

Every offline test runs against fixtures. The failure this project actually
suffers is Goodreads changing its markup, schema, WAF posture, or GraphQL
config — and no fixture-backed test can detect that at any coverage
percentage. The offline suite tells you the code is internally consistent. It
does not tell you the code works.

The suite that answers that question is opt-in and not in CI:

```bash
GOODREADS_LIVE=1 pytest tests/e2e -v
```

It is not in CI on purpose — it hits a third party on every run, which would
be both unreliable as a gate and impolite to an unofficial endpoint. The
tradeoff is real and accepted: **this repo can be fully green and still
broken in production.**

The mitigation is procedural, not mechanical: run the live suite after any
parsing change ([review rubric](review-rubric.md) §2), and before a release.

`server.py` at 74% is the second-order version of the same thing — the
uncovered lines are mostly GraphQL tool bodies whose behavior is only
meaningfully exercised against live data.

## What would actually improve quality

In rough order of value:

1. **Recorded-response tests.** Capture real Goodreads responses and replay
   them, refreshed periodically. Closes the fixtures-drift-from-reality gap
   without hitting the network on every CI run.
2. **A scheduled live run.** The live suite on a timer rather than per-commit
   — catches upstream drift without coupling CI to a third party.
3. Fixture coverage of the `server.py` tool bodies.

Not on the list: raising the coverage gate. It would be satisfied by testing
paths that are already understood, and would not move the number that matters.
