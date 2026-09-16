# Reflections

Durable knowledge about *how work on this repo actually goes* — patterns worth
carrying across sessions that don't fit the other three layers.

The layers, so entries land in the right one:

| file | holds | lifetime |
|---|---|---|
| [`AGENTS.md`](../../AGENTS.md) | standing rules for the code | durable |
| [`.claude/memory/corrections.md`](../../.claude/memory/corrections.md) | a specific thing an agent got wrong | append-only |
| [`.claude/session-summary.md`](../../.claude/session-summary.md) | what's in flight right now | overwritten each session |
| **this directory** | recurring patterns in the work itself | durable |

A correction is "the WAF returns 202, which `raise_for_status()` misses." A
reflection is "documentation about this codebase is wrong more often than the
code is, so verify doc claims against source before writing them."

One file per theme, dated. Delete entries that stop being true.
