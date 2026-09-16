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

## 2026-09-16 — ACMM L3 scaffolding

**Done:** L0 (#1–#6) and L2 (#7–#11, #13, #14) complete and merged; #12 closed
as already satisfied by `.editorconfig` from #35.

**In flight:** L3 PRs open — #41 `.claude/settings.json` (closes #18, #19, #21),
#42 metrics, #43 review rubric, #44 quality doc, and this one (#20).

**Blocked on:** nothing.

**Watch:**
- `client.py`'s module docstring says "four unofficial-but-stable read
  surfaces" and omits the HTML scrape that `list_shelves` uses. The agent docs
  were corrected in #37; the source comment still under-counts. Small, real,
  unfixed.
- L4 (#22–#30) is untouched. Several of those criteria want working GitHub
  Actions and self-tuning scripts, which is a different proposition from the
  docs-and-config work L0–L3 turned out to be. Worth deciding whether the
  machinery is wanted before generating it.
