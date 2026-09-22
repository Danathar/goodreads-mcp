# .claude/

Agent configuration for this repo.

| path | purpose |
|---|---|
| `settings.json` | permissions and hooks — **affects anyone running Claude Code here** |
| `hooks/guard-bash.py` | the `PreToolUse` guard on the allowed Bash verbs — see below |
| `skills/` | repo-specific skills (endpoint checks, tool authoring) |
| `memory/` | correction log — see [memory/README.md](memory/README.md) |

## settings.json

Three permission layers, matching the repo's actual risk profile:

**allow** — routine: `Read`, `pytest`, and the git verbs `status`, `diff`
and `log`. These run without prompting. They are *meant* to be read-only, but
the rules alone don't make them so — see "Know what the allow list can't say"
below, and the guard hook that closes the gap.

**ask** — legitimate but consequential: `git push`, and edits to
`.github/workflows/`. CI changes and publishing are real actions, not
routine ones; they prompt rather than being blocked.

**deny** — never, silently: `git push --force` / `-f` and `git reset --hard`
(history destruction), and reads of `.env` / `.env.*`. This project has no
credentials of its own — it's read-only and unauthenticated — so the deny
list guards against history loss and accidental secret exposure rather than
protecting a secret this repo actually holds.

`git push --force-with-lease` is deliberately **not** denied. It's the safe
variant — it refuses when the remote has moved — and it's the right tool for
amending an open PR branch.

### Know what the deny list can't do

Permission rules are prefix matches, so each destructive command is listed in
both bare and trailing-argument form (`Bash(git push -f)` *and*
`Bash(git push -f *)`). Even so, the list is **not exhaustive**: a flag placed
after other arguments — `git push origin main --force` — does not share a
prefix with any deny entry and slips through.

That's why `Bash(git push *)` sits in `ask`. The prompt on every push is the
real gate — for pushes *issued as tool calls*; the deny entries are a faster,
quieter block on the common spellings. Don't mistake the deny list for
airtight coverage, and note the qualifier: the next section is about what it
leaves out.

### Know what the allow list can't say

The prefix limit cuts both ways, and the allow side is the sharper edge. A
rule's prefix stops at the verb, so everything after it is unconstrained, and
three of the allowed verbs reach well past "read-only" ([#60][60]):

- `Bash(pytest *)` allows `pytest <any path>`. pytest imports what it
  collects, and a path on the command line overrides `testpaths`, so
  `pytest /tmp/anything.py` runs that file's module-level code. Code inside a
  Python process is not a tool call: no `ask` or `deny` rule is consulted for
  what it then does, including pushing with `--force` or reading `.env`.
  `-p`, `-c`, `--rootdir` and `--import-mode` load code or relocate collection
  the same way.
- `Bash(git diff *)` allows `git diff --no-index <a> <b>`, which prints any two
  files on the machine. `Read(./.env)` is a Read-tool rule; a Bash command is
  matched against a different rule set, so the deny is never consulted.
- `Bash(git diff *)` and `Bash(git log *)` accept `--output=<path>`, which
  writes any file. A shell redirection after any of the three verbs does the
  same.

No permission rule can say "bare `pytest`, but not `pytest` with a path", and
no rule can admit `git diff HEAD~1` while refusing `git diff --no-index a b`,
because they share a prefix. So the enforcement is a hook.

[60]: https://github.com/Danathar/goodreads-mcp/issues/60

## The guard hook

`PreToolUse` on `Bash`: [`hooks/guard-bash.py`](hooks/guard-bash.py) is given
the whole command string and **denies** the spellings above —

- `pytest` (or `python -m pytest`) with a path that does not resolve inside
  `tests/`, or with any option outside its safe list;
- any `VAR=value` in front of a guarded verb whose name is not on the guard's
  safe list of variables (`GOODREADS_LIVE`, `GOODREADS_USER_ID`, and colour and
  locale settings). An assignment is part of the command: the shell applies it
  to the process the allow list started. bash's `VAR+=value` append form counts
  — it creates the variable when it is unset — and so does the export family
  (`export VAR=value`, `declare -x`, `typeset -x`, `readonly`), which reaches
  every command bash runs later in the same string;
- a wrapper the guard does not model — `env`, `command`, `exec`, `timeout` and
  the rest — standing in front of a guarded verb. `env VAR=value git diff` puts
  the assignment where the guard reads the verb, and `env -S '...'` hides the
  whole invocation inside one word;
- `--no-index`, `--output` and `--output-file` on `git diff` and `git log`;
- a shell redirection on any of the three verbs (`2>&1` is fine — it names a
  file descriptor, not a file);
- `$`, backticks, a brace expansion, a glob standing where an option goes, or
  a string the shell tokeniser rejects, in a guarded command — the guard can't
  see what the shell would substitute or expand, so it refuses rather than
  guesses.

### The guard only holds if it reads what the shell runs

Two ways that came apart, both of them a bypass, both closed:

- **`#` is not a comment mid-word.** `shlex` ends a token at `#` wherever it
  appears; a shell starts a comment only at the start of a word. So
  `pytest --ignore=z#z /tmp/evil.py` reached the guard as `pytest --ignore=z`,
  which is clean, and reached pytest whole — arbitrary code execution with no
  prompt. The lexer is given no comment character now. A real trailing comment
  is then read as arguments and refused; that is the safe direction to be
  wrong in.
- **Brace expansion happens after the guard has looked.** It spells a denied
  flag out of a token that does not contain one:
  `git diff --no-inde{x,x} a b` is `git diff --no-index --no-index a b` by the
  time git sees it, and `--outpu{t,t}=<path>` writes any file the same way.
  Braces are refused rather than expanded, because an expander that disagreed
  with the shell in the other direction would be this same bug again.
- **An assignment in front of the verb was read by nothing.** The guard popped
  `VAR=value` off the front before it looked at anything, so neither half was
  checked. Not the name, unless the verb was `pytest`:
  `GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD` ran `prog` once per changed
  path, further than `--no-index` or `--output` reach. And not the value, which
  is shell text like any other word, so `FOO=$(...) pytest -q` substituted
  unseen. Assignments are read with the rest of the command now, their names
  against a safe list rather than a list of the dangerous ones — the deny list
  that stood there held seven names and missed `GIT_EXTERNAL_DIFF`,
  `PYTHONWARNINGS` and `LD_PRELOAD` ([#115][115]).

[115]: https://github.com/Danathar/goodreads-mcp/issues/115

The general shape: any construct the guard resolves differently from the shell
is a bypass, not a cosmetic difference. Adding one that makes the guard see
*more* than the shell runs costs a false refusal; one that makes it see *less*
costs the whole gate.

Everything else it leaves alone: it prints nothing, and the call goes through
the normal permission flow. It never *grants* anything, so the usual commands
still run without a prompt — `pytest -q`, `pytest tests/test_parsers.py -k
name`, the coverage line CI uses, `git diff --stat HEAD~1 HEAD`, `git log
--oneline -20`.

It is tested by `tests/test_agent_permissions.py`: a table of denied
spellings, a table of ordinary invocations that must stay silent, a check that
the hook is registered, and a check that every Bash verb on the allow list is
one the guard knows about. Add a verb to `allow` and that last test fails
until the guard has an opinion on it.

Two limits worth knowing. The guard reads the command; it does not follow
`cd`, so `cd tests && pytest .` is refused (the `.` resolves to the repo root)
— write `pytest tests` instead. And it checks *tool calls*: a file that is
already in `tests/` is collected by `pytest -q` like any other, which is why
writing a file still prompts.

## The test hook

`PostToolUse` on `Write|Edit`: when a file under `goodreads_mcp/` changes,
the offline suite runs (~0.5s) and **only speaks up if it fails**, feeding
the failure back as context. Silence means green.

It exits cleanly when `pytest` isn't on PATH, so a checkout without the dev
environment installed isn't punished.

Remember the limit: the offline suite runs on fixtures. A green hook does
**not** mean a parsing change works against live Goodreads — for that, run
`GOODREADS_LIVE=1 pytest tests/e2e -v`. See [AGENTS.md](../AGENTS.md).
