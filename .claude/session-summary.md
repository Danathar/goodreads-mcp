# Session summary

A checkpoint an agent writes at the end of a working session and reads at the
start of the next one. Its job is narrow: carry forward the things that are
true right now and would otherwise have to be rediscovered.

Standing rules go in [AGENTS.md](../AGENTS.md). Things learned by getting them
wrong go in [memory/corrections.md](memory/corrections.md). This file is the
volatile layer — **overwrite it each session**, don't append.

## Format

```markdown
## <date> — <one-line focus>

**Done:** what landed, with PR/issue numbers
**In flight:** open PRs, unmerged branches, anything half-finished
**Blocked on:** decisions or merges needed before the next step
**Watch:** anything observed but not acted on
```

Keep it to what the next session needs. If something is durable, promote it to
AGENTS.md and leave it out of here.

---

## 2026-09-17 — #60, the allow-list guard

**Done:** `.claude/hooks/guard-bash.py`, a `PreToolUse` hook on `Bash`, denies
what the allow list cannot refuse — `pytest` outside `tests/` or with a
code-loading option, `--no-index` / `--output` on `git diff` and `git log`,
redirections on the allowed verbs. Registered in `settings.json`; tables of
denied and permitted spellings plus a registration check in
`tests/test_agent_permissions.py`; `.claude/README.md` and
`docs/SECURITY-AI.md` corrected. Verified against a real `claude -p` session:
`pytest -q /var/tmp/.../notatest.py` denied with the reason, marker file not
written; `pytest -q tests/... -k registered` and `git log --oneline -3` ran
without a prompt. Closes #60 once merged (PR on `sec/60-guard-bash-hook`).

**In flight:** that PR.

**Blocked on:** nothing.

**Watch:**
- The guard does not follow `cd`, so `cd tests && pytest .` is refused; write
  `pytest tests`. Documented in `.claude/README.md`.
- The guard's pytest option safe list is an allowlist. A plugin option nobody
  has used yet (e.g. from pytest-xdist) will be refused until it is added to
  `_PYTEST_LONG` / `_PYTEST_SHORT` — add it with a note on what it reaches.
- `client.py`'s module docstring still says "four unofficial-but-stable read
  surfaces" and omits the HTML scrape that `list_shelves` uses. Unfixed.
