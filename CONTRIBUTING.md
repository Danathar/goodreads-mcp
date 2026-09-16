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

Keep changes focused. Update `pyproject.toml` and `manifest.json` versions
together when publishing a release — CI checks they match.
