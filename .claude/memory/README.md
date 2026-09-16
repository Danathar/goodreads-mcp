# Corrections

Durable notes for AI agents working in this repo: things an agent got wrong,
and the correction. The point is that the same mistake isn't repeated in the
next session, when the conversation that produced it is gone.

Stable conventions belong in [AGENTS.md](../../AGENTS.md). This directory is
for the narrower, easy-to-rediscover-the-hard-way things — especially facts
about Goodreads' unofficial surfaces, which are learned by breaking something.

## Format

One entry per correction, appended to [`corrections.md`](corrections.md):

```markdown
## <short title>
**Date:** YYYY-MM-DD
**Wrong:** what was assumed or done
**Right:** what's actually true
**Why it matters:** the consequence of getting it wrong again
```

Keep entries short. If a correction turns into a standing rule, promote it to
AGENTS.md and leave the entry here as the reason behind the rule.

Delete entries that stop being true — a stale correction is worse than none.
