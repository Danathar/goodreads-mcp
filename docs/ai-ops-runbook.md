# AI operations runbook

This is for the maintainer. For each automated signal this repository has
today, it says how you notice it, what to check first, and what to do. It
covers only what is on `main` now: the workflows under
[`.github/workflows/`](../.github/workflows/), the Dependabot config, the
`PreToolUse` guard under [`.claude/hooks/`](../.claude/hooks/guard-bash.py), and
the pull requests the Hive agents open (see [maintenance.md](maintenance.md)).
[`merge-queue.yml`](../.github/workflows/merge-queue.yml) has no section: it
runs only on a merge queue's `merge_group` event, GitHub offers merge queues
only to organization-owned repositories, and this one is under a personal
account ([branch-protection.md](branch-protection.md#merge-queue)).

Every `gh` command below takes `-R Danathar/goodreads-mcp` when you run it
outside a checkout of this repository. Run the read-only ones as written; the
ones that change something say so.

| signal | section |
|---|---|
| A GitHub issue titled "Nightly live check failing" | [Nightly drift issue](#nightly-drift-issue) |
| Red `test` on a pull request or on `main` | [`test` is red](#test-is-red) |
| A `Release MCPB` run that failed or did nothing | [A release run refused](#a-release-run-refused) |
| A run of `AI fix requested` | [`ai-fix.yml` runs](#ai-fixyml-runs) |
| An agent pull request that is wrong or unwanted | [An agent PR misbehaves](#an-agent-pr-misbehaves) |
| A `guard-bash` denial in a Claude Code session | [`guard-bash` refusals](#guard-bash-refusals) |
| A red `Agent audit trail` run | [Agent audit trail is red](#agent-audit-trail-is-red) |
| A Dependabot pull request | [Dependabot action updates](#dependabot-action-updates) |
| A scheduled run that never appeared | [A scheduled run did not fire](#a-scheduled-run-did-not-fire) |

## Nightly drift issue

**What it is.** [`nightly-compliance.yml`](../.github/workflows/nightly-compliance.yml)
runs the live suite (`pytest tests/e2e`) against real Goodreads at 07:00 UTC
every day. When it fails on a scheduled run, its last step opens an issue, or
comments on the open one:

> Title: `Nightly live check failing — Goodreads may have changed`

The same text is the workflow's `TITLE`. `tests/test_ai_ops_runbook.py` fails if
the two stop matching. The issue body carries the run link and the last 30 lines
of `live.log`.

**How you notice.** The issue, or a red `Nightly compliance` run:

```bash
gh issue list --repo Danathar/goodreads-mcp --state open --limit 1000 --json number,title,author --jq '.[] | select(.title == "Nightly live check failing — Goodreads may have changed" and (.author.login == "app/github-actions" or .author.login == "github-actions[bot]" or .author.login == "github-actions")) | "#\(.number) \(.title)"'
gh run list --workflow nightly-compliance.yml --limit 5
```

The first command lists open issues and keeps only an exact title filed by
GitHub Actions, so it does not depend on GitHub's search index. It prints
nothing when none is open, which is also what you see on a healthy night.

**Check first: which step failed, then what changed in the repository.** A red
nightly is usually Goodreads changing something, not a commit. Do not blame a
commit until the failing step and the diff both point at one.

```bash
RUN=<run id from the list above>
gh run view "$RUN" --json jobs --jq '.jobs[].steps[] | select(.conclusion=="failure") | .name'
gh run view "$RUN" --log-failed
```

- If the failed step is **not** `Run live suite against real Goodreads
  endpoints` (for example `Install package and test deps` or `Read QA tuning
  thresholds`), Goodreads is not the cause. Look at the log and at what the step
  reads (`pyproject.toml`, `.github/auto-qa-tuning.json`).
- If it **is** the live suite, find the last green scheduled run and list what
  changed on the paths the suite exercises since then:

```bash
GOOD=$(gh run list --workflow nightly-compliance.yml --event schedule --status success --limit 1 --json headSha --jq '.[0].headSha')
git fetch origin main
git log --oneline "$GOOD"..origin/main -- goodreads_mcp tests/e2e pyproject.toml .github/auto-qa-tuning.json .github/workflows/nightly-compliance.yml
```

  An empty list means no commit touched anything the suite runs, so it is
  upstream drift. A non-empty list is a lead, not a verdict: read those commits
  against the failing test before you decide. Either way, the failing test name
  in the log, not the commit list, tells you which surface broke.

**What to do.**

1. Reproduce locally: `GOODREADS_LIVE=1 pytest tests/e2e -v`. Re-run once; a
   transient network error or rate limit looks the same. The workflow already
   retries (`.live.retries` in `.github/auto-qa-tuning.json`) before it alarms.
2. Map the symptom to the surface that broke with the
   [`check-live-endpoints`](../.claude/skills/check-live-endpoints/SKILL.md)
   skill. It has the symptom table and the GraphQL-config check.
3. Fix it in a pull request that adds an offline fixture test for the new
   shape (the skill says how). Never hardcode the GraphQL key or endpoint, and
   never evade a WAF challenge ([SECURITY-AI.md](SECURITY-AI.md)). Parsing
   changes need the live suite run before you claim they work ([AGENTS.md](../AGENTS.md)).
4. Nothing closes the issue. After the next scheduled run is green, close it
   yourself: `gh issue close <number> --comment "Green on <run link>."` If you
   close it early, the next red night opens a second issue. Leave the title
   unchanged while it is open, because the workflow finds it by title.

A failed run started by hand (`workflow_dispatch`) opens no issue, by design:
the issue step runs only for `schedule`. Read the run itself.

## `test` is red

`test` is the one job in [`ci.yml`](../.github/workflows/ci.yml). It runs the
offline suite with the coverage floor, then again against the declared
dependency floors. It is the one status check the ruleset on `main` requires
([branch-protection.md](branch-protection.md)).

**On a pull request.** The PR cannot merge under the ruleset until it is green.

```bash
gh pr checks <number>
gh run view <run id> --log-failed
```

Read the log, fix on the PR branch, and let the next push replace the run (a new
push cancels the old one). A failure with no cause in the diff: `gh run rerun
<run id> --failed`. Do not merge around it; the ruleset is the control, and at ACMM L6 agent pull requests auto-merge once it passes.

**On `main`.** Pushes to `main` get one run each, never cancelled.

When that run fails, [`auto-issues.yml`](../.github/workflows/auto-issues.yml)
opens an issue titled `CI failing on main`, or comments on the open one. It
finds the open one by exact title and GitHub Actions as author, and never
closes it:

```bash
gh issue list --repo Danathar/goodreads-mcp --state open --limit 1000 --json number,title,author --jq '.[] | select(.title == "CI failing on main" and (.author.login == "app/github-actions" or .author.login == "github-actions[bot]" or .author.login == "github-actions")) | "#\(.number) \(.title)"'
```

The same workflow opens `Scheduled run failing: Release MCPB` or
`Scheduled run failing: Agent audit trail` when a scheduled run of either
monthly workflow fails, and comments on that issue while it is open. A failed
monthly release most often means main still carries the last release's
version: run `Release MCPB` with `prepare`, merge the version-bump pull
request, then run it again. `Nightly compliance` opens its own drift issue.

Close it yourself once `main` is green again. Each later red push to `main`
adds a comment with that run's link.

```bash
gh run list --workflow ci.yml --branch main --limit 5
gh api --paginate "repos/Danathar/goodreads-mcp/commits/$(git rev-parse origin/main)/check-runs" \
  --jq '.check_runs[] | select(.name=="test") | [.conclusion, .status] | @tsv'
```

Effect on releases: [`release.yml`](../.github/workflows/release.yml)'s
`Require a green test check on this commit` step reads exactly this check on the
commit it releases. A red, unfinished or missing `test` stops the release job
with `Not releasing - the 'test' check on <sha> is not green`. No other check
counts. Fix `main` with a normal pull request, then run the release again (see
below). A red `main` stays red for that commit until a new commit lands, unless
you re-run the failed CI job.

## A release run refused

`Release MCPB` runs at 09:00 UTC on the 1st (or by hand). Find out which step
stopped it:

```bash
gh run list --workflow release.yml --limit 5
gh run view <run id> --json jobs --jq '.jobs[] | select(.conclusion=="failure") | .name, (.steps[] | select(.conclusion=="failure") | "  " + .name)'
gh run view <run id> --log-failed
```

The release job's steps, in order, and what a stop there means. The versioning
rules are in [CONTRIBUTING.md](../CONTRIBUTING.md#releases).

| step that stops | message | do |
|---|---|---|
| `Refuse any commit that is not on main` | `Releases run from main only` | Re-run from `main` in "Use workflow from". |
| `Check the schedule is enabled` | not an error; the summary says monthly releases are off | Set the repository variable `AUTO_RELEASE_ENABLED` to `true` to allow scheduled releases (`gh variable list`). A run by hand ignores it. |
| `Decide whether to release` | not an error; the summary says `No release` | Nothing under `goodreads_mcp/` changed since the last release. Run by hand with `force` if you want one. |
| `Require a green test check on this commit` | `Not releasing - the 'test' check ... is not green` or `no successful 'test' check` | See [`test` is red](#test-is-red). A commit pushed with `github.token`, such as the `prepare` branch, has no `test` run. |
| `Read the version from pyproject.toml` / `Check manifest.json matches pyproject.toml` | version not CalVer, or asked for a different version than the file carries, or the two files differ | Fix through a pull request. This workflow tags what `main` carries and never changes the version. |
| `Validate the version` | tag already exists, not newer than the last release, or suffix and `prerelease` disagree | Run `prepare` for the next version, merge its pull request, then release. |
| `Check the bundle is the one build-mcpb packed` | `the artifact was replaced after it was uploaded` | Treat as a security event. Do not re-run until you know why; see [SECURITY-AI.md](SECURITY-AI.md). |

If the run failed after `Tag the approved commit`, re-run the failed job; it
finishes that release instead of refusing the tag (CONTRIBUTING.md).

To check everything without publishing: `gh workflow run release.yml --ref main
-f dry_run=true` (this starts a run; it tags and publishes nothing).

## `ai-fix.yml` runs

[`ai-fix.yml`](../.github/workflows/ai-fix.yml) runs when a maintainer applies
the `ai-fix-requested` label or comments `/ai-fix`. Today it only gates the
request, posts an acknowledgement comment and writes a summary: its agent step
is `Agent step (not configured)`. No code is changed by it.

```bash
gh run list --workflow ai-fix.yml --limit 10
grep -n "not configured" .github/workflows/ai-fix.yml
```

- **Grey (skipped).** Normal. The event came from a bot (the Hive app applies
  `ai-fix-requested` to the issues it opens) or a `/ai-fix` comment came from
  someone who is not the owner, a member or a collaborator. Nothing to do.
- **Green.** A maintainer asked and an acknowledgement comment was posted on the
  issue or PR. Read that comment (`gh issue view <number> --comments`), then do
  the work yourself; the workflow will not.
- **Red** with `lacks write access ... refusing`. The requester is not a
  collaborator with write access. This is the control working. Check
  `gh api repos/Danathar/goodreads-mcp/collaborators/<actor>/permission --jq .permission`
  only if you expected that person to be allowed.

If the `grep` stops printing a match, the agent step has been wired up since
this was written. That job holds `contents: write`, so read the workflow before
you rely on this section.

## An agent PR misbehaves

Agent pull requests are opened by the Hive GitHub App (author
`app/danathar-atomic-hive`) or by `Danathar`, on branches named `<role>/<slug>`,
and each body ends with a `— hive: agent=...` line. List the open ones:

```bash
gh pr list --author app/danathar-atomic-hive --state open
gh pr view <number> --json author,headRefName,labels --jq '{author: .author.login, branch: .headRefName, labels: [.labels[].name]}'
```

Stop it, smallest step first:

1. **Do not merge.** At ACMM L6 non-outreach agent PRs auto-merge when checks
   pass, so act quickly. The `hold` label (applied to outreach PRs, see
   maintenance.md) is a marker; the ruleset on `main` requires `test` and no
   approval, so the label alone does not block a merge.
2. **Close it:** `gh pr close <number> --comment "Closing: <reason>"`. This
   changes the PR. It does not delete the branch, and it does not stop the agent
   opening another.
3. **Ask the agents to leave it alone.** The repository has a label
   `hive-pause/hive-wild-mole`, described as "Hive dashboard hold: agents will
   not act on this item until an operator removes this label." Apply it with
   `gh pr edit <number> --add-label hive-pause/hive-wild-mole`. That
   description is Hive's; this repository has no code that reads the label.
4. **Stop the app.** In GitHub: Settings → Applications → Installed GitHub Apps
   → the Hive app (`danathar-atomic-hive`) → Configure → Suspend, or Uninstall to
   revoke it. Suspending stops its pushes and API calls to this repository at
   once. [SECURITY-AI.md](SECURITY-AI.md) does not describe this procedure, and
   it needs the web UI (`gh api /user/installations` refuses a normal token), so
   check the path on the page when you need it.
5. **Confirm `main` could not have been written to.** The ruleset should still
   be active: `gh api repos/Danathar/goodreads-mcp/branches/main --jq .protected`
   prints `true`, and `gh api repos/Danathar/goodreads-mcp/rulesets` lists
   `protect main` with `"enforcement": "active"`
   ([branch-protection.md](branch-protection.md)). The ruleset does not stop a
   token that merges a green pull request through the API, so also look at what
   merged recently: `gh pr list --state merged --limit 10`.

A PR that touches `.claude/settings.json` or `.claude/hooks/**` is a change to
the agent permission boundary; read it line by line and merge it yourself or not
at all ([risk-tiers.md](risk-tiers.md), Tier 2).

## `guard-bash` refusals

[`guard-bash.py`](../.claude/hooks/guard-bash.py) is registered as a
`PreToolUse` hook on `Bash` in [`.claude/settings.json`](../.claude/settings.json).
When it refuses, the agent's tool call is denied with a reason and the agent
sees that reason. A person running Claude Code here sees the same denial.

Reproduce a refusal, without running the command:

```bash
echo '{"tool_input":{"command":"pytest -p evil tests"},"cwd":"."}' | python3 .claude/hooks/guard-bash.py
```

It prints a JSON `permissionDecision: "deny"` with the reason (here, `-p evil`
imports a module as a pytest plugin). An allowed command prints nothing. The
hook exits 0 either way; the decision is in the output.

**What to do.** Read the reason and rewrite the command without the construct it
names (a path outside `tests/`, a loader option, a redirection, a variable
assignment in front of the verb). Do not loosen the guard or the allow list to
get past it. If the denial looks wrong, or a message says `guard-bash.py failed
(...)`, the hook failed closed: open an issue, and treat any change to the hook
as Tier 2 ([SECURITY-AI.md](SECURITY-AI.md)). The table of denied and allowed
spellings is in `tests/test_agent_permissions.py`:

```bash
pytest tests/test_agent_permissions.py -q
```

## Agent audit trail is red

[`agent-audit.yml`](../.github/workflows/agent-audit.yml) runs monthly (05:23
UTC on the 1st) or by hand, reads every pull request merged in the window, and
writes one row per agent pull request to the run summary. It fails for one
finding: an agent pull request that touched `.claude/settings.json` or
`.claude/hooks/**` and was merged by a bot, an app or an unknown account.
Missing signature lines and commits without `Signed-off-by` are listed in the
summary but do not fail it; a merge commit from updating a branch from `main`
is not counted as missing the trailer. A merge is treated as one only on a
pull request into `main`, and only when every parent after the first is
outside the pull request's own commits. So merging another unmerged branch in
still needs the trailer, even as a branch's first commit. It also stops with exit 2
when `since` is not a real `YYYY-MM-DD` date, when the window holds 500 or more
merged pull requests, or when one pull request reaches GitHub's 250-commit or
3000-file list cap.

```bash
gh run list --workflow agent-audit.yml --limit 3
gh run view <run id> --log-failed
```

**What to do.** For a finding, open the named pull request and read the change
to the permission boundary line by line, as if it were unmerged. If it should
not have landed, revert it in a pull request you merge yourself, then find out
how a bot merged it ([An agent PR misbehaves](#an-agent-pr-misbehaves), step 5).
For the 500 cap, re-run with a later `since`. That run covers only the newer
part, so also read the older part some other way before you call the window
audited. The workflow takes no end date.

## Dependabot action updates

[`.github/dependabot.yml`](../.github/dependabot.yml) asks for one grouped pull
request a week, covering every GitHub Action the workflows `uses:`, with commit
prefix `ci`. Every `uses:` is pinned to a 40-character SHA with a `# vN`
comment, and Dependabot moves both together.

**No pull request ever appears?** This repository is a fork, and GitHub does not
run Dependabot version updates on a fork until they are enabled in the fork
itself: Insights, Dependency graph, Dependabot in the web UI (repository admin;
the API does not expose it). Committing `dependabot.yml` is not enough. That
page also shows the log of the last run, which says why a run opened nothing.
Until the switch is on, bump the pinned actions by hand (#365).

```bash
gh pr list --author app/dependabot --state open
gh pr diff <number>
gh pr checks <number>
```

Check first that each changed `uses:` line still has a full SHA and a `# vN`
comment (`tests/test_action_pins.py` fails the PR if not), and that the new
version is the one you expect for that action. `release.yml` and `ai-fix.yml`
hold `contents: write`; read the upstream release notes for any action they
call before merging. A bump is a workflow change, so a person reads it like any
other ([review-rubric.md](review-rubric.md)). If a bump breaks a workflow, close
the PR and pin the previous SHA; Dependabot will propose the next version.

## A scheduled run did not fire

Three workflows are scheduled: `nightly-compliance.yml` (daily 07:00 UTC),
`agent-audit.yml` (05:23 UTC on the 1st) and `release.yml` (09:00 UTC on the
1st). GitHub may start a scheduled run some minutes late. First tell "did not
run" from "ran and did nothing":

```bash
gh run list --workflow nightly-compliance.yml --event schedule --limit 3
gh run list --workflow agent-audit.yml --event schedule --limit 3
gh run list --workflow release.yml --event schedule --limit 3
gh workflow list --all
```

- **No run at all, or the workflow is not listed as `active`.** GitHub disables
  scheduled workflows in a repository with no activity for 60 days. Re-enable
  the one that stopped: `gh workflow enable nightly-compliance.yml` (or
  `agent-audit.yml`, or `release.yml`). This changes the workflow's state.
- **A green `Release MCPB` run that tagged nothing.** Not a missed run. See the
  `Check the schedule is enabled` and `Decide whether to release` rows in
  [A release run refused](#a-release-run-refused); `gh variable list` shows
  `AUTO_RELEASE_ENABLED`.

To run the missed workflow now, dispatch that workflow by name from `main`:

```bash
gh workflow run nightly-compliance.yml --ref main   # live suite; opens no drift issue
gh workflow run agent-audit.yml --ref main -f since=<YYYY-MM-DD>   # read-only; blank since means the last 31 days
gh workflow run release.yml --ref main -f dry_run=true   # checks everything, publishes nothing
```

Each starts a run. The nightly one reads Goodreads, so do not repeat it
casually ([AGENTS.md](../AGENTS.md), "Be a polite guest"). The release
workflow's real release is `gh workflow run release.yml --ref main`, only after
the version pull request is merged.
