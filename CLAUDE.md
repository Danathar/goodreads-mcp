# CLAUDE.md

See **[AGENTS.md](AGENTS.md)** for how to work in this repo — architecture,
the read-only constraint, the WAF workaround, runtime GraphQL config, and
testing. That file is the source of truth; this one only adds Claude-specific
notes.

## Quick reference

```bash
pytest -q                               # offline suite (what CI runs)
GOODREADS_LIVE=1 pytest tests/e2e -v    # live suite (run after parsing changes)
```

Non-negotiables: read-only (no auth, no writes), parse `__NEXT_DATA__` not the
DOM, fetch book pages via the `.xml` path (WAF), never hardcode the GraphQL
key or endpoint.

## Running the server locally

```bash
python3.10 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/goodreads-mcp
```
