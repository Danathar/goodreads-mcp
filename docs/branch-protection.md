# Branch protection

`main` is what `release.yml` packs and publishes. This file says what protects
it, why each rule is there, and how to check that GitHub is really enforcing
it.

## Status

The ruleset below is committed but **not yet applied** (#148). Until an admin
applies it, `main` takes a direct push from any token with `contents: write`.
Check it yourself; neither call needs admin rights:

```bash
gh api repos/Danathar/goodreads-mcp/branches/main --jq .protected
gh api repos/Danathar/goodreads-mcp/rulesets
```

Once it is applied, the first prints `true` and the second lists `protect
main`. `false` and `[]` mean `main` is unprotected.

## Why it matters here

The README says a human reviews and merges everything, and every gate in this
repository assumes that: the hold label, the [review rubric](review-rubric.md),
the [risk tiers](risk-tiers.md) and the coverage gate in `ci.yml` all sit behind
a pull request. Nothing makes anyone open one.

Two tokens here can write `main`:

- The Hive GitHub App the README describes. It pushes branches for the agents
  that open pull requests. Those agents read issue and PR text written by bots
  and third parties, and [SECURITY-AI.md](SECURITY-AI.md) calls that text input
  to judge, not instruction. "Never push to `main`" is an instruction to them,
  not a control.
- `ai-fix.yml`, which holds `contents: write`. Its agent step is not wired up
  yet. Once it is, a model that reads issue bodies holds that token.

`release.yml` publishes whatever `main` carries. It no longer runs on a push
(#165), but its monthly run, or anyone's run by hand, tags the version in
`pyproject.toml` and publishes a `.mcpb` built from that tree. A direct push
that bumps the version is released at the next run, and no person has read
the change.

## The ruleset

[`.github/rulesets/main.json`](../.github/rulesets/main.json) is the agreed
definition. It is in GitHub's import format, so it applies as-is. What each rule
does:

- **Targets `~DEFAULT_BRANCH`**, so it follows a rename of `main`.
- **No bypass actors.** A bypass for Actions or for an App hands back the direct
  push this exists to stop.
- **`deletion` and `non_fast_forward`** stop `main` being deleted or rewritten.
- **`pull_request` with 0 approvals.** GitHub does not let anyone approve their
  own pull request. On a single-maintainer repository, requiring one approval
  means nothing can ever merge, including the change that relaxes the rule.
  What 0 still enforces is that every change arrives as a pull request, and
  the required check below makes it one that passed `test`. It does not make
  the merger a person: a token that can write contents, like the Hive App's,
  could merge a green pull request through the API. Until there is a second
  reviewer, who presses merge is a rule, not a setting.
- **One required check, `test`.** It is the job in `ci.yml`, which runs on
  every pull request to `main` with no path filter, so no pull request waits
  for a check that never starts. `release` runs on a schedule or by hand and `labeler`
  classifies a change rather than checking it. `integration_id` 15368 is
  GitHub Actions. `tests/test_branch_ruleset.py` fails if the job is renamed
  or gains a filter. One case still waits: GitHub starts no `pull_request`
  run while a pull request conflicts with `main`, and if `main` then moves so
  the conflict goes away, nothing starts one (#105 merged that way, with no
  CI run at all). Merge `main` into the branch, or close and reopen the pull
  request, and `test` runs.

Nothing in this repository pushes to `main` outside a pull request today. Every
first-parent commit on `main` since 2026-08-01 is a pull request merge, and the
last direct pushes were syncs from `shreeyachand/goodreads-mcp` on 2026-09-12
and 2026-09-14. So applying this should change nothing about how work lands,
except that bringing in upstream changes now takes a pull request too.

## Applying it

A pull request cannot change repository settings. A repository admin applies
it once, with:

```bash
gh api --method POST repos/Danathar/goodreads-mcp/rulesets \
  --input .github/rulesets/main.json
```

To change it later, edit the file through a pull request, then update the live
ruleset from the file, using the id the first call returned:

```bash
gh api --method PUT repos/Danathar/goodreads-mcp/rulesets/RULESET_ID \
  --input .github/rulesets/main.json
```

## When there is a second reviewer

Set `required_approving_review_count` to 1. Consider
`require_last_push_approval`, so a push after approval needs a fresh one.
