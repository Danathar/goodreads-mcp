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

## 2026-09-18 — #71, the guard read a different command than the shell ran

**Done (#60, earlier):** `.claude/hooks/guard-bash.py`, a `PreToolUse` hook on
`Bash`, denies what the allow list cannot refuse — `pytest` outside `tests/` or
with a code-loading option, `--no-index` / `--output` on `git diff` and
`git log`, redirections on the allowed verbs. Merged.

**Done (#71):** two ways that guard read a shorter or different command than
bash would run, each of them restoring all of #60 with no prompt:
`shlex`'s default `commenters = '#'` truncated at a mid-word `#`
(`pytest --ignore=z#z /tmp/evil.py` arrived as `pytest --ignore=z`), and brace
expansion rebuilt a denied flag after the hook had decided
(`git diff --no-inde{x,x} a b`). The lexer now has no comment character;
`{`, `}` join `$` and backticks as refused, and a glob in option position is
refused too. 16 new `_DENIED` rows and 3 new `_PERMITTED` rows in
`tests/test_agent_permissions.py`; both reproductions verified end to end
against real `git` 2.47.3 and real `pytest` before and after.

**In flight:** the PR on `sec/guard-shell-expansion`.

**Blocked on:** nothing.

**Watch:**
- The guard does not follow `cd`, so `cd tests && pytest .` is refused; write
  `pytest tests`. Documented in `.claude/README.md`.
- The guard's pytest option safe list is an allowlist. A plugin option nobody
  has used yet (e.g. from pytest-xdist) will be refused until it is added to
  `_PYTEST_LONG` / `_PYTEST_SHORT` — add it with a note on what it reaches.
- Refusing braces means a legitimate `{a,b}` in a guarded command is blocked
  outright, not prompted. Nothing in the repo's documented invocations uses
  one; if that changes, expand rather than refuse only with an expander tested
  against bash in both directions.
- `.github/workflows/ci.yml` is the one workflow with no `permissions:` block,
  so it takes the repository default while `docs/SECURITY-AI.md` says to scope
  every job to its minimum. Observed, not acted on.
- `client.py`'s module docstring still says "four unofficial-but-stable read
  surfaces" and omits the HTML scrape that `list_shelves` uses. Unfixed.
