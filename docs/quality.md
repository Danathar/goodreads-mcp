# Quality

The state of quality assurance in this repo: what's enforced mechanically,
what's known-weak, and where the real risk sits.

**Last reviewed:** 2026-09-20

## What runs automatically

| gate | where | blocking |
|---|---|---|
| offline test suite | `ci.yml` on every push/PR to `main` | yes |
| coverage floor, 55% | `ci.yml` (`--cov-fail-under`) | yes |
| offline suite at the declared dependency floors (`uv pip install --resolution lowest-direct`) | `ci.yml` | yes |
| MCPB manifest validation | `ci.yml` | yes |
| every workflow `uses:` pinned to a full commit SHA with its tag as a comment, one pin per action, and a Dependabot `github-actions` entry to move them (`tests/test_action_pins.py`) | `ci.yml` | yes |
| version sync (`pyproject` vs `manifest`) | `release.yml` | yes, at release |
| bundle carries no compiled module (it declares three platforms) | `release.yml` | yes, at release |
| packed bundle starts under the manifest's own `uv run` command | `release.yml` | yes, at release |
| released `.mcpb` is the one `build-mcpb` packed (sha256 job output, checked after download) | `release.yml` | yes, at release |
| published wheel and sdist are the ones `build-pypi` built (sha256 job output, checked after download) | `release.yml` | yes, at release |
| release refused unless the run is on `main`, the commit is on `main`, `test` passed on it and no run of `test` is red or unfinished (no other check counts), and the version is CalVer, untagged and newer than the last release | `release.yml` | yes, at release |
| one tracking issue opened, or commented on, when `CI` fails on a push to `main`, and one per workflow when a scheduled `Release MCPB` or `Agent audit trail` run fails (open issues listed, exact title and Actions-bot author; never closed automatically) | `auto-issues.yml` | no, notification only |
| live suite against the real Goodreads endpoints, daily at 07:00 UTC, retried before it counts as a failure; a failed scheduled run opens, or comments on, one drift issue | `nightly-compliance.yml` | no, notification only |
| monthly read-back of merged agent pull requests; the run fails only when one that touched `.claude/settings.json` or `.claude/hooks/**` was merged by a bot, an app or an unknown account | `agent-audit.yml` | no, report only |
| automated code review | Codex, every PR | advisory |
| offline tests on source edit | `.claude/settings.json` hook | advisory, local |

## Current numbers

| | |
|---|---|
| offline tests | 1715 passing, 25 skipped (live, opt-in) |
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

The suite that answers that question is the live suite, and it is not part
of the per-commit CI:

```bash
GOODREADS_LIVE=1 pytest tests/e2e -v
```

It is kept out of `ci.yml` on purpose — it hits a third party on every run,
which would be both unreliable as a gate and impolite to an unofficial
endpoint. The tradeoff is real and accepted: **this repo can be fully green
and still broken in production.**

What runs it instead is
[`nightly-compliance.yml`](../.github/workflows/nightly-compliance.yml): once
a day, against the real endpoints, opening a drift issue when it fails. That
surfaces upstream drift within a day, but it is a notification, not a gate —
nothing blocks a merge or a release on it. Between nightly runs the
mitigation is procedural: run the live suite after any parsing change
([review rubric](review-rubric.md) §2), and before a release.

`server.py` reaching 100% doesn't change this: full statement coverage from
fixtures still can't exercise how the GraphQL tool bodies behave against
Goodreads' actual, changing responses.

## What would actually improve quality

In rough order of value:

1. **Recorded-response tests.** Capture real Goodreads responses and replay
   them, refreshed periodically. Closes the fixtures-drift-from-reality gap
   without hitting the network on every CI run.
2. Fixture coverage of the `server.py` tool bodies.

Not on the list: raising the coverage gate. It would be satisfied by testing
paths that are already understood, and would not move the number that matters.
