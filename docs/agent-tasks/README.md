# Agent task traceability

How to start from a change in this repository and find the agent task that
produced it, using only marks this repository actually carries. Each mark
below was checked against the history at `main` = `3beacbb` (PRs up to #278);
none of them is enforced by a gate, so every one is **sometimes missing**, and
the last section says how often.

Every command names `Danathar/goodreads-mcp`. This repository is a fork, and
in a clone with an `upstream` remote a bare `gh` reads the parent repository
instead (see [`docs/metrics.md`](../metrics.md)).

## The marks

### 1. The `— hive:` signature line on a PR

Pull requests opened by the Hive agents end their body with one line:

```text
— hive: agent=quality backend=claude model=claude-opus-5-5 effort=medium claude=2.1.287
```

`agent=` is the role (`architect`, `guide`, `quality`, `scanner`, `sec-check`
appear in this history), `backend=` the harness that ran it, `model=` the model.
`effort=` and `claude=` (CLI version) are not always present.

Two variants exist, so a bare `— hive:` prefix does not identify a Hive agent:

- **Role-less signature**, e.g. `— hive: backend=omp model=anthropic/claude-opus-5-5 effort=high`.
  Written on PRs the maintainer (`Danathar`) opened from an agent session. No
  `agent=`, so no role: these PRs cannot be attributed to a role from the PR
  alone.
- **No signature.** Most of the maintainer's PRs, and one Hive-app PR (#200,
  whose body instead ends `*Filed by scanner agent (ACMM L6)…*`).

Who opened the PR is the other half of the mark. The Hive's PRs are opened by
the GitHub App `danathar-atomic-hive`. `gh pr view` prints its login as
`app/danathar-atomic-hive`; the REST API prints `danathar-atomic-hive[bot]`.
Match that login, not "any bot": `github-actions[bot]` also commits here.

Read it back:

```bash
# one PR: who opened it, and its signature line
gh pr view <N> --repo Danathar/goodreads-mcp --json author,body \
  --jq '.author.login, (.body | split("\n") | map(select(startswith("— hive: "))))'
```

### 2. The branch name `<prefix>/<slug>`

The branch is `<prefix>/<slug>`, but **the prefix is not the role.** In the 47
merged PRs that carry both an `agent=` role and a prefixed branch, the prefix
equals the role in 30 and differs in 17. The mapping observed (counts from the
[2026-10-03 ledger](2026-10-03.md)):

| role in the signature | branch prefixes used (merged PRs) |
|---|---|
| `quality` | `quality/` (19), `test/` (4) |
| `sec-check` | `sec/` (8) — the prefix drops `-check` |
| `guide` | `guide/` (6) |
| `architect` | `architect/` (2), `fix/` (2) |
| `scanner` | `scanner/` (3), `docs/` (1), `feat/` (1), `fix/` (1) |

The maintainer's branches use topic prefixes with no role at all: `fix/`,
`docs/`, `ci/`, `test/`, `release/`, `sec/`, `feat/`, plus a few `security/`,
`quality/`, bare `fix-issue-N`, and the early `l0/`…`l4/` prefixes (ACMM level
scaffolding). A `sec/` or `quality/` branch can therefore be maintainer work.
Treat the prefix as a hint and the signature `agent=` as the role.

A prefix followed by an issue number (`ci/237-isolate-mcpb-build`,
`fix/229-find-book-root-query`) names the issue the work answers.

```bash
# which PR did a branch belong to? (works after the branch is deleted)
gh pr list --repo Danathar/goodreads-mcp --state all --head <prefix>/<slug> --json number,author
# merge commits keep the branch name
git log main --first-parent --merges --grep='from Danathar/<prefix>/<slug>$' --format='%h %s'
```

### 3. `Signed-off-by:` on commits

Commits carry `Signed-off-by:` (128 of 368 reachable from `3beacbb`), but a role
appears as the sign-off name on only 13 of them (`quality` 5, `guide` 3,
`architect` 3, `sec-check` 2; `scanner` never). Mostly the sign-off names a
person (`Danathar`, `Douglas Baggett`) or the app (`danathar-atomic-hive[bot]`,
7). Role-named sign-offs use `<role>@hive.kubestellar.io` in 11 of the 13.
So a role sign-off is evidence the commit came from that role, but 13 of 368
commits is far too few to rely on its absence meaning anything.

```bash
git log <sha> -1 --format='%an <%ae>%n%(trailers:key=Signed-off-by,valueonly)'
```

### 4. `Hive-Run:` / `Hive-Plan:` / `Hive-Spec:` trailers

These exist here, on **8 commits**, all authored by `Danathar`. Each sits on
one of the 8 PRs whose signature says `backend=omp` (#176, #177, #179, #181,
#190, #194, #235, #238); none sits on a Hive-app PR. They come as a set:

```text
Hive-Run: Danathar/goodreads-mcp#237
Hive-Plan: issue #237 recommendation (build-mcpb / verify-mcpb split)
Hive-Spec: goodreads-mcp-release-isolation#237
```

`Hive-Run:` names `<repo>#<number>` of the **issue** being worked (in the
example #237; the PR that closed it is #238), not the PR. `Hive-Plan:` and
`Hive-Spec:` are free text naming the plan and spec the run followed; nothing
in this repository resolves them. The 48 Hive-app PRs carry none of the
three.

```bash
# commits carrying a Hive-Run, and the issue each names
git log main --grep='^Hive-Run: ' --format='%h %(trailers:key=Hive-Run,valueonly)'
# the PR that contains a given commit
gh api repos/Danathar/goodreads-mcp/commits/<sha>/pulls --jq '.[].number'
```

### 5. `Closes #n` in the PR body

The closing reference joins a PR to the issue that asked for the work, and so
to the issue's own history. The platform records it:

```bash
gh pr view <N> --repo Danathar/goodreads-mcp --json closingIssuesReferences \
  --jq '[.closingIssuesReferences[].number]'
```

Merge commits are `Merge pull request #N from Danathar/<branch>` (every one of
the 139 merged PRs up to #278 has one on the first-parent line), so
`git log main --first-parent --grep='^Merge pull request #<N> '` finds the commits of PR N, and
`git log <merge>^1..<merge>^2` lists the branch's own commits.

## Following one change end to end

```text
file/line --git blame--> commit --gh api commits/<sha>/pulls--> PR #N
PR #N --signature + author--> role / backend / model
PR #N --closingIssuesReferences--> issue (the task statement)
commit --Hive-Run trailer--> issue number (only the 8 commits above)
```

## What a ledger is

A file in this directory named `YYYY-MM-DD.md` is one **reading** of these
marks over a **pinned** range (PRs up to a stated number, commits reachable
from a stated SHA), with the exact command under every number. It is the same
idea as [`docs/metrics/`](../metrics/2026-09-24.md): the file is left as it was
read, never edited to follow the repository, because the next reading is a new
dated file. The commands are written to reproduce the numbers later:
`gh api --paginate` with an explicit upper PR number, never `gh pr list
--limit N` (which silently truncates a "pinned" range once the repository has
more than N PRs), and `git log <sha>` for the commit side.

One limit on reproducing: PR bodies and branch names can be edited after the
fact, so the PR-side numbers are pinned by range, not frozen; the commit-side
numbers are immutable.

## How often the marks are missing

See the [2026-10-03 ledger](2026-10-03.md): of 139 merged PRs up to #278, 67
carry a `— hive:` line (47 with a role), 72 carry none. Do not read
"unsigned" as "human-written": the signature is a convention the author
chooses to write, and nothing here checks that it was.
