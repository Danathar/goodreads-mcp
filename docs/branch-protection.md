# Branch protection

`main` is what `release.yml` packs and publishes. This file says what protects
it, why each rule is there, and how to check that GitHub is really enforcing
it.

## Status

The ruleset below is **active**. An admin applied it on 2026-09-24 (#148) as
ruleset `23955646`, and the live rules match the committed file. Check it
yourself; neither call needs admin rights:

```bash
gh api repos/Danathar/goodreads-mcp/branches/main --jq .protected
gh api repos/Danathar/goodreads-mcp/rulesets
```

The first prints `true` and the second lists `protect main` with
`"enforcement": "active"`. `false`, `[]` or any other enforcement means the
ruleset was removed or disabled, and `main` takes a direct push from any token
with `contents: write` again.

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

`release.yml` publishes whatever `main` carries, and only that. It no longer
runs on a push (#165), but its monthly run, or anyone's run by hand, tags the
version in `pyproject.toml` and publishes a `.mcpb` built from that tree. A
direct push that bumps the version is released at the next run, and no person
has read the change. A run by hand from any other branch stops at the job's
first step, which checks that the run is on the default branch and that the
commit is on it at the remote (#174); the same job refuses a commit with no
passing `test` check, which is what the head of a branch pushed with
`github.token` looks like.

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
- **Branches must be up to date before merging**
  (`strict_required_status_checks_policy: true`, applied to the live ruleset
  on 2026-10-07). GitHub refuses to merge a pull request whose branch does not
  contain `main`'s current head, so `test` has run on what will land. Hive
  merges this repository through its serialized merge lane
  (`merge_strategy: hive-serialized`): one pull request at a time is merged
  with `main` and re-tested, so the rule costs one CI run per merge, not one
  per open pull request.
- **A push to `main` never has its `test` run cancelled or queued out.**
  `ci.yml` gives every push to `main` its own concurrency group, keyed on the
  commit's SHA, and only cancels a pull request's own in-progress run.
  `cancel-in-progress: false` alone would not be enough: GitHub Actions keeps
  at most one *pending* run per group and replaces it when another is queued,
  so a shared group could still leave an intermediate main commit's run
  cancelled. Without the per-commit group, a later push could pre-empt an
  earlier main commit's `test` run — and `release.yml` refuses to release a
  commit whose `test` check is not green, so a pre-empted run would silently
  block that commit from ever being released.

Nothing in this repository pushes to `main` outside a pull request today. Every
first-parent commit on `main` since 2026-08-01 is a pull request merge, and the
last direct pushes were syncs from `shreeyachand/goodreads-mcp` on 2026-09-12
and 2026-09-14. So applying it changed nothing about how work lands, except
that bringing in upstream changes now takes a pull request too.

## Applying it

A pull request cannot change repository settings. A repository admin applied
it once, on 2026-09-24, with:

```bash
gh api --method POST repos/Danathar/goodreads-mcp/rulesets \
  --input .github/rulesets/main.json
```

To change it, edit the file through a pull request, then update the live
ruleset from the file. `23955646` is the id that first call returned:

```bash
gh api --method PUT repos/Danathar/goodreads-mcp/rulesets/23955646 \
  --input .github/rulesets/main.json
```

## Merge queue

**The merge queue is not enabled, and cannot be.** GitHub offers merge queues
only to repositories owned by an organization, and this one is under a
personal account. The ruleset has no `merge_queue` rule.

The gap it would close is closed another way: the up-to-date rule above makes
every pull request carry `main`'s current head before it merges, and Hive's
serialized merge lane merges one pull request at a time. A merge queue would
do the same by building a temporary branch of the pull request on top of
`main`'s current head and merging only if the required checks pass there.

[`.github/workflows/merge-queue.yml`](../.github/workflows/merge-queue.yml) is
the half that can live in the tree. It runs only on `merge_group`, an event
GitHub sends only when a queue is on, so today it never runs. It has one job,
`test`, with the same steps as `ci.yml`'s `test` job. That is what makes the
queue's commit report the `test` context the ruleset requires; a queue enabled
without such a workflow would wait for a check that never starts and block
every merge.

`release.yml` still works behind a queue, with one consequence to know. Its
gate reads every check run named `test` on the commit. It refuses the release
if any of them has a conclusion other than success, skipped or neutral (a
failed, cancelled or still-pending run counts, even next to a green one), and
it also refuses if none succeeded. The queue's commit is the one that lands
on `main`, so it carries the queue's `test` run and the one `ci.yml` starts on
the push to `main`. Both must be clean for the release to go ahead; a red or
unfinished duplicate blocks it.

If the repository ever moves to an organization, to enable it:

1. In a pull request, add a `merge_queue` rule to
   `.github/rulesets/main.json`. Pick its parameters (merge method, group
   sizes, timeouts) in that review. Keep the required check as `test`.
2. After it merges, an admin updates the live ruleset from the file with the
   `gh api --method PUT .../rulesets/23955646` call under "Applying it".

`merge-queue.yml` has to keep matching `ci.yml`, or the queue gates on
something other than what pull requests gate on.
`tests/test_merge_queue_workflow.py` compares the trigger, the job id and every
step, so change both files together. Nothing has exercised the workflow on a
real `merge_group` event; the first queued pull request will.

## When there is a second reviewer

Set `required_approving_review_count` to 1. Consider
`require_last_push_approval`, so a push after approval needs a fresh one.
