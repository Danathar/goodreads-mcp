# .claude/

Agent configuration for this repo.

| path | purpose |
|---|---|
| `settings.json` | permissions and hooks — **affects anyone running Claude Code here** |
| `skills/` | repo-specific skills (endpoint checks, tool authoring) |
| `memory/` | correction log — see [memory/README.md](memory/README.md) |

## settings.json

Three permission layers, matching the repo's actual risk profile:

**allow** — read-only and routine: `Read`, `pytest`, and the read-only git
verbs (`status`, `diff`, `log`). These run without prompting.

**ask** — legitimate but consequential: `git push`, and edits to
`.github/workflows/`. CI changes and publishing are real actions, not
routine ones; they prompt rather than being blocked.

**deny** — never, silently: `git push --force` and `git reset --hard`
(history destruction), and reads of `.env` / `.env.*`. This project has no
credentials of its own — it's read-only and unauthenticated — so the deny
list guards against history loss and accidental secret exposure rather than
protecting a secret this repo actually holds.

## The test hook

`PostToolUse` on `Write|Edit`: when a file under `goodreads_mcp/` changes,
the offline suite runs (~0.5s) and **only speaks up if it fails**, feeding
the failure back as context. Silence means green.

It exits cleanly when `pytest` isn't on PATH, so a checkout without the dev
environment installed isn't punished.

Remember the limit: the offline suite runs on fixtures. A green hook does
**not** mean a parsing change works against live Goodreads — for that, run
`GOODREADS_LIVE=1 pytest tests/e2e -v`. See [AGENTS.md](../AGENTS.md).
