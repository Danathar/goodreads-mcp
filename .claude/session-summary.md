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

## 2026-10-04 — the nightly drift issue's exact lookup (#279)

**Done:** `nightly-compliance.yml` now finds its open drift issue the way
`auto-issues.yml` does: list open issues (`--limit 1000`, no `--search`), keep
the one with exactly `$TITLE` opened by the Actions bot. The `gh` stub that
applies a step's `--jq` filter to a fixture now lives in
`tests/_workflow_steps.py` (`GH_ISSUE_LIST`, `ACTIONS_BOT_LOGINS`, `issue`),
shared by both workflow test files. Earlier (2026-10-03): the ACMM L6 gaps
#280–#287, all merged.

**In flight:** the #279 pull request. It changes `.github/workflows/`, so a
person merges it.

**Blocked on:** #247 is closed; whether its A/B/C choice (remembering a
refused key that comes back unchanged) was made is not recorded here.

**Watch:**
- Codex P2s left open: `auto-issues.yml`'s concurrency group keeps one pending
  run, so a third red push while two are queued drops one comment (#283);
  `risk-config.json` extraction gaps (#281); ledger and doc wording (#284, #285).
- The `.mcpb` ships `.claude/` and `docs/` whole; `.mcpbignore` excludes
  neither. Harmless public text today, but every new agent file ships too.
- The 2026-10-01 scheduled `release.yml` run (36840547035) ended `failure`;
  not investigated.
