# Contributing

## Setup

```bash
python3.10 -m venv .venv
.venv/bin/pip install -e ".[test]"
```

## Test

```bash
pytest -q
```

Offline tests (`tests/`, minus `tests/e2e/`) run by default and are what CI
checks. The live suite under `tests/e2e/` hits real Goodreads endpoints and
is opt-in:

```bash
GOODREADS_LIVE=1 pytest tests/e2e -v
```

If you change how a tool parses Goodreads' HTML/JSON, run the live suite —
offline tests use fixtures and won't catch a markup change on their own.

## Scope

This server is read-only and unauthenticated by design (no login, no
cookies, no writes). PRs adding write operations or requiring credentials
are out of scope.

## Pull requests

Keep changes focused. Don't change the version in a feature pull request;
releases have their own (see below).

## Releases

Releases are automatic and monthly, numbered by date: CalVer `YYYY.M.PATCH`,
such as `2026.10.0`, `2026.10.1`, `2026.11.0`. Pre-releases end `b1` or `rc1`.
Tags have no `v` prefix. The `0.1.x` line is retired, and `2026.x` sorts above
it. Everything runs in [`release.yml`](.github/workflows/release.yml):

1. **Propose a version.** Run the workflow by hand with `prepare` ticked. It
   takes the next number from the date (or the `version` you give it), sets it
   in `pyproject.toml` and `manifest.json`, pushes `release/<version>` and opens
   the pull request. Merging that pull request approves the number.
2. **Release.** On 09:00 UTC on the 1st of each month, or when run by hand
   without `prepare`, the workflow tags the version the merged `pyproject.toml`
   carries, attaches the `.mcpb` to a GitHub release, publishes to PyPI, and
   lists the PyPI release on the official MCP registry as
   `io.github.Danathar/goodreads-mcp-ai` from `server.json`. That file keeps the
   placeholder version `0.0.0`; the workflow writes the released number into
   a copy at publish time, so there is no third version to bump.
   It never changes the version itself, and it only ever tags a commit that
   is on `main`: run it from `main` in "Use workflow from", or it stops before
   doing anything.

The scheduled run does nothing until the repository variable
`AUTO_RELEASE_ENABLED` is `true`; a run by hand always proceeds. A release is
skipped when nothing under `goodreads_mcp/` changed since the last release;
tick `force` to release anyway (for a dependency-only change, say). It is
refused when the run is not on `main`, when the commit's checks are not green,
when the commit has no passing `test` check, when the two files disagree,
when a `version` given by hand is not the one `pyproject.toml` carries, when
the version is not CalVer, is already tagged or is not newer than the last
release, or when a `b1`/`rc1` suffix and the `prerelease` box disagree.
Tick `dry_run` to run every check and build the bundle without tagging or
publishing.

A change to `.claude/settings.json` or `.claude/hooks/**` is a change to the
agent permission boundary. A human reads it and merges it, whoever wrote it
(see [risk tiers](docs/risk-tiers.md), Tier 2).
