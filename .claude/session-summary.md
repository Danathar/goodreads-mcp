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

## 2026-09-24 — #148, `main` had no protection behind "a human merges everything"

**Done:** `main` had no branch protection and no ruleset, so the hive App or
`ai-fix.yml`'s token could push straight to it and start `release.yml`. Added
`.github/rulesets/main.json` (default branch, no bypass, no delete or
force-push, pull request with 0 approvals, required check `test`),
`docs/branch-protection.md`, a Tier 2 entry for `.github/rulesets/**`, a
"Never push to `main`" hard rule in SECURITY-AI.md, and
`tests/test_branch_ruleset.py`. Offline count row 881 → 884.

**In flight:** the PR on `sec/protect-main`.

**Blocked on:** an admin applying the ruleset after merge (command in
`docs/branch-protection.md`). #148 closes only when
`gh api repos/Danathar/goodreads-mcp/branches/main --jq .protected` prints `true`.

**Watch:** renaming the `test` job in `ci.yml` or adding a path filter to its
`pull_request` trigger fails `tests/test_branch_ruleset.py`, on purpose.
