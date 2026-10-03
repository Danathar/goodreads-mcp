# How several agents share this repository

More than one AI agent works here, next to the maintainer. This page says
which agents, how work reaches them, what keeps them from colliding, and who
decides what lands. [`AGENTS.md`](../AGENTS.md) is the brief every agent reads
first. This page is about how they fit together, not about how to write code
here.

It holds no counts, because they go stale. Each fact below comes with a
command you can run to see the current answer.

## There is no dispatcher in this repository

Nothing in this repository decides which agent runs, when, or on what. No
workflow, script or config here schedules an agent. [Hive](https://github.com/hivecommons/hive)
does that from outside, through a GitHub App, and its dashboard is not part of
this repository. [`maintenance.md`](maintenance.md) describes the arrangement.

The only agent-related workflow here is
[`ai-fix.yml`](../.github/workflows/ai-fix.yml), covered below. Everything else
an agent does arrives as an ordinary issue, comment, branch or pull request.

## Who works here

Agents leave three marks you can read.

**The signature.** Pull requests that Hive opens end with a line like
`— hive: agent=quality backend=... model=... effort=...`. List the agents that
signed pull requests from the Hive app, with a count each:

```bash
gh pr list -R Danathar/goodreads-mcp --state all --limit 1000 \
  --author app/danathar-atomic-hive --json body \
  --jq '.[].body | capture("— hive: agent=(?<a>[a-z-]+)").a' | sort | uniq -c
```

At the time of writing this prints five roles: `architect`, `guide`,
`quality`, `scanner` and `sec-check`. `--limit 1000` is a cap, not a promise.
If `gh pr list -R Danathar/goodreads-mcp --state all --limit 1000 --json number --jq length`
prints 1000, the list was cut short and the counts are a lower bound.

**The branch prefix.** Hive branches are `<role>/<slug>`: `quality/`,
`scanner/`, `sec/`, `architect/` and `guide/` appear. The prefix does not
always match the signature. `quality` has opened `test/…` branches, `scanner`
has opened `fix/…`, `feat/…` and `docs/…` branches, and `architect` has opened
`fix/…` branches. Trust the signature, and check the author is the Hive app,
because the maintainer also uses `fix/`, `docs/`, `ci/` and `test/`.

**The labels.** Hive files issues with `agent/<role>` labels for provenance.
List them:

```bash
gh label list -R Danathar/goodreads-mcp --limit 200 --json name \
  --jq '.[].name | select(startswith("agent/"))'
```

Four of those labels (`agent/strategist`, `agent/ci-maintainer`,
`agent/security`, `agent/contributor`) have no matching signature among the
pull requests above. That is what the history shows, not a statement about
what those agents can do.

What the signed pull requests do, read from their titles:

| role | typical pull request titles |
|---|---|
| `scanner` | fixes, packaging and CI updates |
| `quality` | tests, and docs that say what the tests cover |
| `sec-check` | security fixes and the ruleset |
| `architect` | structural changes, such as the Python floor |
| `guide` | documentation |

This is a reading of titles, not a contract. Hive decides what each role
does, not this repository.

The maintainer is the other contributor. The history also holds pull requests
from `Danathar` that carry no signature. Nothing says a human wrote every one
of them; the signature is what marks a Hive pull request.

## How work reaches an agent

- **An issue.** Hive reads issues and files its own. Issues it files carry
  `agent/<role>` labels. Issues from the ACMM evaluation also carry `acmm`.
- **A comment or label on an issue or pull request.** Applying the
  `ai-fix-requested` label, or a `/ai-fix` comment from the owner, a member or
  a collaborator, starts [`ai-fix.yml`](../.github/workflows/ai-fix.yml).
- **Hive's own schedule**, outside this repository.

### What `ai-fix.yml` does today

It runs on `issues: labeled` and `issue_comment: created`. It skips an event
sent by a bot, and skips a `/ai-fix` comment from anyone who is not the owner,
a member or a collaborator. For an event that passes, it:

1. checks the actor has `admin` or `write` permission, and fails if not;
2. checks out the repository;
3. comments on the issue with the constraints in
   [`review-rubric.md`](review-rubric.md) and `AGENTS.md`: read-only and
   unauthenticated, and run the live suite before claiming a parsing fix;
4. writes a job summary saying no agent is wired up.

**It does not run an agent.** The last step is a placeholder that says so in
the summary. Wiring one needs an `ANTHROPIC_API_KEY` secret and a deliberate
decision, so applying the label today only gets a comment. The Hive app applies
`ai-fix-requested` to ACMM issues itself, and the workflow skips it because the
sender is a bot. A person who wants the workflow to run applies the label or
comments `/ai-fix`.

The workflow's own header explains why it is a human trigger: review findings
need checking before anyone applies them.

## What keeps agents from colliding

There is no lock. Agents work on separate branches, and collisions are found
by reading, then settled by a person.

- **Claimed ground.** Agent pull requests say which files they claim, in the
  body, as `Claimed ground:` or as a list of files, and often add an
  `Overlap check:` line naming open pull requests that touch the same files.
  Find them:

  ```bash
  gh pr list -R Danathar/goodreads-mcp --state all --limit 1000 \
    --json number,body --jq '.[] | select(.body | test("Claimed ground|Overlap check")) | .number'
  # a total at or above 1000 means the list was cut short
  gh pr list -R Danathar/goodreads-mcp --state all --limit 1000 --json number --jq length
  ```

  This is a habit of the agents that Hive runs, not a rule enforced here:
  nothing in this repository checks a pull request for it.
- **The `hold` label.** Hive puts `hold` on pull requests it opens (not on
  every one), and they wait for a person. No workflow in this repository
  applies or reads `hold`; Hive does. List what is waiting now:

  ```bash
  gh pr list -R Danathar/goodreads-mcp --state open --label hold
  ```

  An empty list means nothing is held at that moment. A `hive-pause/<hive>`
  label is the Hive dashboard's stop: its description says agents will not act
  on an item that carries it until an operator removes it. `needs-human` has
  no description here. In this history it sits on issues such as #148, #153
  and #260, which wait for a person.
- **`ai-fix.yml` queues rather than cancels.** Its concurrency group is the
  issue number, so two requests on one issue run one after the other.
- **Merge order.** This repository holds no merge queue or ordering rule. Where
  two pull requests touch the same files, the person merging decides which
  goes first.

## Who decides what lands

A person reviews and merges everything. Nothing auto-merges.

- **`main` is protected.** An active ruleset requires a pull request and
  forbids deleting or rewriting `main`, with no bypass actors. See
  [`branch-protection.md`](branch-protection.md). Check it:

  ```bash
  gh api repos/Danathar/goodreads-mcp/branches/main --jq .protected
  gh api repos/Danathar/goodreads-mcp/rulesets --jq '.[] | [.name, .enforcement] | @tsv'
  ```

  Expect `true` and `protect main	active`.
- **Risk tiers set how hard a person looks.** Each pull request names its tier
  in the body. See [`risk-tiers.md`](risk-tiers.md).
- **An agent's permission boundary needs a person.** A change to
  `.claude/settings.json` or `.claude/hooks/**` is read and merged by a human,
  whoever wrote it (`CONTRIBUTING.md`, risk-tier 2). An agent must not widen
  its own boundary ([`SECURITY-AI.md`](SECURITY-AI.md)).
- **An outside commenter cannot start the write-capable workflow.** The
  `ai-fix.yml` gate above is what keeps them from using its `contents: write`.

## If you are an agent

Read `AGENTS.md`, then `.claude/session-summary.md`. Say in your pull request
which files you claim and which open pull requests you checked against. A
person decides the rest.
