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

## 2026-10-03 — the ACMM L6 gaps (#269–#276)

**Done:** one pull request per gap, all merged: `merge-queue.yml` (#280,
inert until an admin adds a `merge_queue` rule), `.claude/risk-config.json`
(#281, joined to `docs/risk-tiers.md` by `tests/test_risk_config.py`),
`docs/strategy.md` (#282), `auto-issues.yml` (#283, one `CI failing on main`
issue per red push to `main`, found by exact title and Actions-bot author),
`docs/agent-tasks/` (#284), `docs/multi-agent.md` (#285),
`docs/ai-ops-runbook.md` (#286), `agent-audit.yml` (#287, monthly, fails only
when an agent change to `.claude/settings.json` or `.claude/hooks/**` was
merged by a bot). `tests/test_action_pins.py` now exempts named action-free
workflows (`_NO_ACTIONS`).

**In flight:** nothing.

**Blocked on:** #279, the nightly drift issue's fuzzy `--search` lookup, needs
a person to land (workflow change). `auto-issues.yml` already uses the exact
lookup #279 proposes. #247 is closed; whether its A/B/C choice (remembering a
refused key that comes back unchanged) was made is not recorded here.

**Watch:**
- Codex P2s left open: `auto-issues.yml`'s concurrency group keeps one pending
  run, so a third red push while two are queued drops one comment (#283);
  `risk-config.json` extraction gaps (#281); ledger and doc wording (#284, #285).
- The `.mcpb` ships `.claude/` and `docs/` whole; `.mcpbignore` excludes
  neither. Harmless public text today, but every new agent file ships too.
- The 2026-10-01 scheduled `release.yml` run (36840547035) ended `failure`;
  not investigated.
