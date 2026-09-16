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

**deny** — never, silently: `git push --force` / `-f` and `git reset --hard`
(history destruction), and reads of `.env` / `.env.*`. This project has no
credentials of its own — it's read-only and unauthenticated — so the deny
list guards against history loss and accidental secret exposure rather than
protecting a secret this repo actually holds.

`git push --force-with-lease` is deliberately **not** denied. It's the safe
variant — it refuses when the remote has moved — and it's the right tool for
amending an open PR branch.

### Know what the deny list can't do

Permission rules are prefix matches, so each destructive command is listed in
both bare and trailing-argument form (`Bash(git push -f)` *and*
`Bash(git push -f *)`). Even so, the list is **not exhaustive**: a flag placed
after other arguments — `git push origin main --force` — does not share a
prefix with any deny entry and slips through.

That's why `Bash(git push *)` sits in `ask`. The prompt on *every* push is the
real gate; the deny entries are a faster, quieter block on the common
spellings. Don't mistake the deny list for airtight coverage.

## The test hook

`PostToolUse` on `Write|Edit`: when a file under `goodreads_mcp/` changes,
the offline suite runs (~0.5s) and **only speaks up if it fails**, feeding
the failure back as context. Silence means green.

It exits cleanly when `pytest` isn't on PATH, so a checkout without the dev
environment installed isn't punished.

Remember the limit: the offline suite runs on fixtures. A green hook does
**not** mean a parsing change works against live Goodreads — for that, run
`GOODREADS_LIVE=1 pytest tests/e2e -v`. See [AGENTS.md](../AGENTS.md).
