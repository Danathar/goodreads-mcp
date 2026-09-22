"""Pin the agent permission rules, the guard hook, and what the docs claim.

`.claude/settings.json` is the only thing standing between an agent working in
this checkout and the developer's machine. Nothing read it until #62, so it
could change -- or its documented meaning could drift away from it -- with no
test noticing. That drift is exactly what produced #60: `.claude/README.md`
called the allow list "read-only and routine" when three of its five entries
are not read-only at all, and the wrong description is what made the gap
invisible for as long as it was.

Three things are pinned here:

* **The rule inventory.** A verb added to `allow` -- or a `deny` entry quietly
  dropped -- fails these tests. `_ALLOW_REACH` forces the addition to come with
  a written answer to "what does this reach?", which is the question the three
  existing offenders were never asked, and the guard has to know the verb.
* **The guard.** `.claude/hooks/guard-bash.py` is what actually closes #60: a
  `PreToolUse` hook on `Bash` that refuses the spellings the allow list cannot
  refuse. `_DENIED` is the table of those spellings, each with the fragment of
  reason the agent is shown; `_PERMITTED` is the table of ordinary invocations
  the hook must stay silent on, including the exact line CI runs. Both tables
  go through `decide()` directly, and one test drives the script as Claude Code
  does -- JSON on stdin, a decision on stdout -- so the contract is covered too.
  A test that the hook is *registered* sits next to them, because an unregistered
  guard passes every table and guards nothing.
* **The documented limits.** `docs/SECURITY-AI.md` and `.claude/README.md`
  record that the prefix-match limit cuts both ways and that the allow side is
  the sharper edge. A later edit that reverts either to the comfortable version
  goes red here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS = _ROOT / ".claude" / "settings.json"
_GUARD = _ROOT / ".claude" / "hooks" / "guard-bash.py"
_SECURITY_AI = _ROOT / "docs" / "SECURITY-AI.md"
_CLAUDE_README = _ROOT / ".claude" / "README.md"

# Every entry on the allow list, against what it can actually reach. The note is
# the point of the table: an entry cannot be added without writing one, and the
# three that are not read-only say so rather than being filed under the verb a
# reader expects. Asserted exhaustive in both directions below.
#
# Sources for the three non-obvious notes, all reproduced at ff8c574 (#60):
#   pytest    -- a path on the command line overrides `testpaths` and is collected
#                regardless of the `test_*.py` convention; import runs module-level
#                code, and code inside a Python process is not a tool call.
#   git diff  -- `--no-index a b` diffs two arbitrary paths with no repository;
#                `--output=<path>` writes one.
#   git log   -- `--output=<path>` is accepted here too.
#
# The guard hook narrows each Bash entry; the note records the reach *without*
# it, because that is what the rule itself grants and what comes back the day
# the hook is unregistered.
_ALLOW_REACH = {
    "Read": "the Read tool; narrowed by the Read(./.env) deny entries",
    "Bash(pytest *)": "arbitrary code execution -- pytest imports what it collects",
    "Bash(git status *)": "read-only in itself; a shell redirection still writes",
    "Bash(git diff *)": "arbitrary file read (--no-index) and write (--output=)",
    "Bash(git log *)": "arbitrary file write (--output=)",
}

_ASK = {
    "Bash(git push *)",
    "Edit(./.github/workflows/**)",
    "Write(./.github/workflows/**)",
}

_DENY = {
    "Bash(git push --force)",
    "Bash(git push --force *)",
    "Bash(git push -f)",
    "Bash(git push -f *)",
    "Bash(git reset --hard)",
    "Bash(git reset --hard *)",
    "Read(./.env)",
    "Read(./.env.*)",
}


def _permissions() -> dict[str, list[str]]:
    return json.loads(_SETTINGS.read_text(encoding="utf-8"))["permissions"]


def test_the_settings_file_is_committed_and_parses():
    """A rule file that has been renamed away guards nothing, silently."""
    assert _SETTINGS.is_file(), f"{_SETTINGS} is missing"
    assert set(_permissions()) >= {"allow", "ask", "deny"}


def test_the_allow_list_is_the_recorded_set():
    """A new allow verb must arrive with a note saying what it reaches.

    Exhaustive in both directions: an entry the table does not cover is an
    unexamined grant, and a table row with no rule behind it is a stale note
    that reads as coverage.
    """
    allowed = set(_permissions()["allow"])
    assert allowed == set(_ALLOW_REACH), (
        "the allow list and its reach table disagree: "
        f"ungrafted={sorted(allowed - set(_ALLOW_REACH))}, "
        f"noted but not granted={sorted(set(_ALLOW_REACH) - allowed)}"
    )


@pytest.mark.parametrize("entry", sorted(_ALLOW_REACH))
def test_every_allow_entry_records_what_it_reaches(entry: str):
    """An empty note records no decision; it only silences the test above."""
    assert _ALLOW_REACH[entry].strip(), f"{entry} has no reach note"


def test_the_ask_list_is_the_recorded_set():
    assert set(_permissions()["ask"]) == _ASK


def test_the_deny_list_is_the_recorded_set():
    """Both spellings of each destructive command, and both `.env` reads.

    Prefix matching is why each destructive command is listed bare *and* with a
    trailing `*`; dropping either half is a silent narrowing.
    """
    assert set(_permissions()["deny"]) == _DENY


def _hooks() -> dict:
    return json.loads(_SETTINGS.read_text(encoding="utf-8")).get("hooks", {})


def test_the_test_hook_is_still_registered():
    """The PostToolUse hook feeds test failures back after an edit."""
    matchers = [entry.get("matcher") for entry in _hooks().get("PostToolUse", [])]
    assert "Write|Edit" in matchers, f"PostToolUse matchers are {matchers}"


# ----------------------------------------------------- the guard hook (#60)


def _load_guard():
    spec = importlib.util.spec_from_file_location("guard_bash", _GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard():
    return _load_guard()


def test_the_guard_is_registered_as_a_pretooluse_hook_on_bash():
    """An unregistered guard passes every table below and guards nothing.

    Pinned to the shape Claude Code reads: a `PreToolUse` entry whose matcher
    is `Bash` and whose command runs the script. `$CLAUDE_PROJECT_DIR` is how
    the path survives a session started from a subdirectory.
    """
    assert _GUARD.is_file(), f"{_GUARD} is missing"
    entries = [e for e in _hooks().get("PreToolUse", []) if e.get("matcher") == "Bash"]
    assert entries, "no PreToolUse hook is registered for Bash"
    commands = [h.get("command", "") for e in entries for h in e.get("hooks", [])]
    assert any(
        "$CLAUDE_PROJECT_DIR/.claude/hooks/guard-bash.py" in c for c in commands
    ), f"guard-bash.py is not among the Bash PreToolUse commands: {commands}"


def test_every_bash_verb_on_the_allow_list_is_one_the_guard_knows(guard):
    """A new Bash verb cannot be allowed without the guard having an opinion.

    The reach table above makes a new entry say what it reaches; this makes it
    say whether the guard covers it. `Bash(curl *)` would fail here until the
    hook grows a `curl` branch -- or a reason is written for why it needs none.
    """
    verbs = {
        entry[len("Bash(") : -len(" *)")]
        for entry in _permissions()["allow"]
        if entry.startswith("Bash(") and entry.endswith(" *)")
    }
    assert verbs, "no Bash(<verb> *) entries found -- has the rule shape changed?"
    assert verbs <= guard.GUARDED, (
        f"allowed but unguarded: {sorted(verbs - guard.GUARDED)}; "
        "add a branch to .claude/hooks/guard-bash.py or remove the rule"
    )


# The variables that may be set in front of a guarded verb. An allow list for
# the same reason the pytest options are one: the deny list it replaced held
# seven names and missed `GIT_EXTERNAL_DIFF`, `PYTHONWARNINGS` and `LD_PRELOAD`,
# each of which runs code in the process the allow list started (#115).
_SAFE_ENV = {
    "GOODREADS_LIVE",
    "GOODREADS_USER_ID",
    "CI",
    "TZ",
    "LANG",
    "LC_ALL",
    "NO_COLOR",
    "FORCE_COLOR",
    "PY_COLORS",
}


def test_the_environment_safe_list_is_the_recorded_set(guard):
    """Adding a variable here is a grant, so it has to be a deliberate edit.

    Exhaustive in both directions, like the allow-list table above: a name the
    guard admits and this set does not is an unexamined grant.
    """
    assert guard.SAFE_ENV == _SAFE_ENV, (
        f"admitted but not recorded: {sorted(guard.SAFE_ENV - _SAFE_ENV)}, "
        f"recorded but not admitted: {sorted(_SAFE_ENV - guard.SAFE_ENV)}"
    )


# Each row is (command, fragment of the reason the agent is shown). The first
# three groups are the reproductions from #60, verbatim; the rest are the
# spellings an agent could reach for once the obvious one is refused.
_DENIED = [
    # 1. pytest is arbitrary code execution
    ("pytest -q /tmp/proof/notatest.py", "outside tests/"),
    ("pytest /tmp/proof/notatest.py", "outside tests/"),
    ("python -m pytest /tmp/proof/notatest.py", "outside tests/"),
    ("python3 -m pytest -q /tmp/proof/notatest.py", "outside tests/"),
    ("pytest .", "outside tests/"),
    ("pytest goodreads_mcp", "outside tests/"),
    ("pytest ../elsewhere/notatest.py", "outside tests/"),
    ("pytest tests/../scripts/x.py", "outside tests/"),  # resolves past tests/
    ("pytest -p some_module", "imports a module"),
    ("pytest -psome_module tests", "imports a module"),
    ("pytest -c /tmp/evil.ini tests", "not on the guard's safe list"),
    ("pytest --rootdir=/tmp tests", "not on the guard's safe list"),
    ("pytest --rootdir /tmp tests", "not on the guard's safe list"),
    ("pytest --import-mode=importlib tests", "not on the guard's safe list"),
    ("pytest --confcutdir=/tmp tests", "not on the guard's safe list"),
    ("pytest --pyargs os", "not on the guard's safe list"),
    ("pytest -o python_files=*.py tests", "not on the guard's safe list"),
    ("pytest --override-ini=addopts=-pevil tests", "not on the guard's safe list"),
    ("pytest --doctest-modules tests", "not on the guard's safe list"),
    ("pytest --pdbcls=evil:Cls tests", "not on the guard's safe list"),
    ("pytest --basetemp=/tmp/gone tests", "not on the guard's safe list"),
    ("pytest --junitxml=/tmp/out.xml tests", "not on the guard's safe list"),
    ("pytest --cov-report=html:/tmp/cov tests", "writes files"),
    ("PYTEST_ADDOPTS='-p evil' pytest -q", "changes what it loads"),
    ("PYTEST_PLUGINS=evil pytest -q", "changes what it loads"),
    ("PYTHONPATH=/tmp pytest -q", "changes what it loads"),
    # an assignment in front of the verb is part of the command: the shell
    # applies it to the process the allow list started, whatever the verb (#115)
    ("LD_PRELOAD=/tmp/evil.so pytest -q", "changes what it loads"),
    # LD_PRELOAD reaches every guarded verb's loader, not pytest's alone (#121:
    # a scanner reproduced the pytest-only row above against `git status` and
    # found #115's fix already covered it -- this pins that it stays covered)
    ("LD_PRELOAD=/tmp/evil.so git status", "changes what it loads"),
    ("PYTHONWARNINGS=ignore::evil.W pytest -q", "changes what it loads"),
    ("GIT_EXTERNAL_DIFF=/tmp/evil.sh git diff HEAD~1 HEAD", "changes what it loads"),
    (
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external "
        "GIT_CONFIG_VALUE_0=/tmp/evil.sh git diff HEAD~1 HEAD",
        "changes what it loads",
    ),
    ("GIT_DIR=/tmp/elsewhere/.git git log -1", "changes what it loads"),
    ("GIT_WORK_TREE=/tmp/elsewhere git status", "changes what it loads"),
    # and its value is shell text like any other word
    ("FOO=$(cat /tmp/x) pytest -q", "cannot see"),
    ("FOO=`cat /tmp/x` pytest -q", "cannot see"),
    ("FOO=`id` git diff HEAD~1", "cannot see"),
    ("FOO={a,b} pytest -q", "cannot see"),
    ("GOODREADS_LIVE=$(id) pytest tests/e2e", "cannot see"),
    # 2. git diff reads any file
    ("git diff --no-index /etc/hostname /dev/null", "--no-index"),
    ("git diff --stat --no-index a b", "--no-index"),
    ("git --no-pager diff --no-index a b", "--no-index"),
    # 3. git diff and git log write any file
    ("git diff --output=/tmp/W.txt HEAD~1 HEAD", "--output"),
    ("git diff --output /tmp/W.txt HEAD~1 HEAD", "--output"),
    ("git diff --output-file=/tmp/W.txt HEAD~1 HEAD", "--output-file"),
    ("git log -1 -p --output=/tmp/L.txt", "--output"),
    # shell redirections on any of the verbs
    ("pytest -q > /tmp/out.txt", "redirection"),
    ("pytest -q >> /tmp/out.txt", "redirection"),
    ("pytest -q &> /tmp/out.txt", "redirection"),
    ("pytest -q 2> /tmp/err.txt", "redirection"),
    ("pytest -q >| /tmp/out.txt", "redirection"),
    ("pytest -q < /etc/passwd", "redirection"),
    ("pytest tests <(cat /tmp/x)", "redirection"),
    ("git log --oneline > /tmp/log.txt", "redirection"),
    ("git status > /tmp/s.txt", "redirection"),
    ("git diff HEAD~1 >/tmp/d.txt", "redirection"),
    # what the guard cannot see through
    ("pytest $(cat /tmp/x)", "cannot see"),
    ("pytest `cat /tmp/x`", "cannot see"),
    ("pytest $HOME/x.py", "cannot see"),
    ("pytest -k \"it's", "could not parse"),
    # brace expansion spells a denied flag out of a token that does not
    # contain one -- the shell assembles it after the guard has looked
    ("git diff --no-inde{x,x} /etc/hostname /dev/null", "cannot see"),
    ("git diff --{n,n}o-index a b", "cannot see"),
    ("git diff --outpu{t,t}=/tmp/W.txt HEAD~1 HEAD", "cannot see"),
    ("git log -1 --outpu{t,t}=/tmp/L.txt", "cannot see"),
    ("pytest tests{,/../../tmp/notatest.py}", "cannot see"),
    # a glob standing where an option goes matches whatever is on disk
    ("git diff --no-inde[x] a b", "glob in an option"),
    ("git log -1 --outpu?=/tmp/L.txt", "glob in an option"),
    ("pytest --cov-repor?=html:/tmp/cov tests", "glob in an option"),
    # `#` is a comment to shlex wherever it appears and to a shell only at the
    # start of a word; the guard must read the whole command either way
    ("git diff a#b --no-index /etc/hostname /dev/null", "--no-index"),
    ("git diff a#b --output=/tmp/W.txt HEAD~1 HEAD", "--output"),
    ("pytest --ignore=z#z /tmp/proof/notatest.py", "outside tests/"),
    ("pytest --deselect=z#z /tmp/proof/notatest.py", "outside tests/"),
    ("pytest -q#q /tmp/proof/notatest.py", "not on the guard's safe list"),
    # bash's append form. `NAME+=value` creates the variable when it is
    # unset, so this is the same environment as `NAME=value`; the guard's
    # `^[A-Za-z_][A-Za-z0-9_]*=` did not match it and the word went on to be
    # read as the verb, which is not one the guard knows, so it returned.
    ("GIT_EXTERNAL_DIFF+=/tmp/prog git diff HEAD~1 HEAD", "safe list"),
    ("LD_PRELOAD+=/tmp/x.so pytest -q", "safe list"),
    ("PYTHONWARNINGS+=ignore::this.W pytest --version", "safe list"),
    ("PYTEST_PLUGINS+=evil pytest -q", "safe list"),
    # the export family: the same assignment written after the verb instead
    # of in front of it, which bash applies to every later command of the
    # string
    ("export GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD~1 HEAD", "export-family"),
    ("declare -x GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD", "export-family"),
    ("typeset -x LD_PRELOAD=/tmp/x.so; pytest -q", "export-family"),
    ("readonly PYTHONPATH=/tmp; pytest -q", "export-family"),
    ("export LD_PRELOAD=/tmp/x.so && pytest -q", "export-family"),
    ("export GIT_EXTERNAL_DIFF=/tmp/prog\ngit diff HEAD", "export-family"),
    # a wrapper the guard does not model, in front of a guarded verb. `env`
    # put the assignment where the guard reads the verb; `env -S` hides the
    # whole invocation inside one word.
    ("env GIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD~1 HEAD", "runs another command"),
    ("env -S 'GIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD'", "runs another command"),
    ("env -S'LD_PRELOAD=/tmp/x.so pytest -q'", "runs another command"),
    ("timeout 60 GIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD", "runs another command"),
    ("command GIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD", "runs another command"),
    ("exec pytest -q", "runs another command"),
    # a guarded verb hidden behind a separator is still checked
    ("git log -1; pytest /tmp/x.py", "outside tests/"),
    ("git log -1 && pytest /tmp/x.py", "outside tests/"),
    ("git log -1\npytest /tmp/x.py", "outside tests/"),
    ("pytest tests/;git diff --no-index a b", "--no-index"),
    ("(pytest -q); git diff --output=/tmp/x", "--output"),
    # --- the corpus pass of #120 -------------------------------------------
    # An operand outside the checkout is `--no-index` without the option:
    # given two paths with at least one outside the working tree, `git diff`
    # prints both files whole. Verified against git 2.55.0 in a throwaway
    # repository -- `git diff .env /etc/hostname` printed the .env, which
    # `Read(./.env)` exists to withhold, and the string starts `git diff ` so
    # `Bash(git diff *)` approves it with no prompt and no option for
    # `_GIT_DENIED` to match.
    ("git diff .env /etc/hostname", "outside the repository"),
    ("git diff /etc/hostname .env", "outside the repository"),
    ("git diff ~/.ssh/id_rsa ~/.bashrc", "outside the repository"),
    # the same word rewriting on pytest's own target check
    ("pytest ~/evil.py", "outside tests/"),
    ("git diff ~/.env HEAD", "outside the repository"),
    ("git status /etc", "outside the repository"),
    ("git log /etc/hostname", "outside the repository"),
    # `-O` is `--orderfile` short, and git takes it clustered (`-pO<file>`
    # runs). It opens a path of the caller's choosing to read sort patterns.
    ("git diff -O/etc/passwd HEAD", "outside the repository"),
    ("git diff -pO/etc/passwd HEAD", "outside the repository"),
    ("git diff --orderfile=/etc/passwd HEAD", "outside the repository"),
    # the scan stops at a letter whose value is the rest of the cluster, and
    # `-u` is not one in `git diff` -- it is `--patch`, so the O after it is
    # the option (`git diff -uO<path>` opens <path>, git 2.55.0)
    ("git diff -uO/etc/passwd HEAD", "outside the repository"),
    # every word after `--` is an operand, a leading `-` included: with a
    # directory named `-` in the checkout, this prints /etc/hostname
    ("git diff -- -/../../etc/hostname .env", "outside the repository"),
    # a word is an option's value only where git reads it as one.
    # `--decorate-refs` is a log option, and `git diff` reads the word after
    # it as a path; `--relative` takes an attached value only, so the word
    # after it stays an operand (`git diff --relative <outside> <inside>`
    # prints the outside file, git 2.55.0)
    ("git diff --decorate-refs /etc/hostname .env", "outside the repository"),
    ("git diff --relative /etc/hostname .env", "outside the repository"),
    # bash takes a redirection in front of the command name as readily as
    # after it. The operator used to be read as the command's name, so the
    # guard fell through its dispatch and charged the write to nothing.
    (">goodreads_mcp/server.py git diff HEAD", "redirection"),
    (">/tmp/out.txt git diff HEAD", "redirection"),
    ("2>/tmp/err.txt pytest -q", "redirection"),
    ("< /etc/passwd pytest -q", "redirection"),
    # a path-qualified command name runs the same tool the bare one does
    ("/usr/bin/git diff --no-index .env /etc/hostname", "outside the repository"),
    ("/usr/bin/python3 -m pytest /tmp/evil.py", "outside tests/"),
    # `export NAME` exports the name and a later command assigns to it, and
    # `set -a` exports every assignment after it without naming a builtin at
    # all -- both reach the verb with no assignment in front of it to find.
    ("export GIT_EXTERNAL_DIFF; GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD", "export-family"),
    ("set -a; GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD", "export-family"),
    ("set -o allexport; LD_PRELOAD=/tmp/x.so; pytest -q", "export-family"),
]

# Ordinary invocations the guard must not touch. The first line is the exact
# CI command; the AGENTS.md ones follow. The last few are commands the hook has
# no opinion on at all -- it must not become a general Bash gate.
_PERMITTED = [
    "pytest -q --cov=goodreads_mcp --cov-report=term-missing --cov-fail-under=55",
    "pytest -q",
    "GOODREADS_LIVE=1 pytest tests/e2e -v",
    "GOODREADS_USER_ID=12345678 GOODREADS_LIVE=1 pytest tests/e2e -v",
    "CI=1 pytest -q",
    "TZ=UTC git log -1",
    # An export reaches what bash runs after it and nothing earlier, and an
    # export in a string with no guarded verb in it is not this hook's
    # business at all.
    "git diff HEAD; export GIT_EXTERNAL_DIFF=/tmp/prog",
    "export FOO=1",
    "export FOO=1; echo hi",
    "declare -x FOO=1; echo hi",
    "export; echo hi",  # sets nothing
    "declare -p; echo hi",  # sets nothing
    "echo export FOO=1",  # the word as text, not as a command name
    # A wrapper with nothing the guard guards behind it.
    "env FOO=1 echo hi",
    "timeout 5 echo hi",
    "pytest",
    "pytest tests",
    "pytest tests/",
    "pytest tests/test_parsers.py",
    "pytest tests/test_parsers.py::test_name",
    "pytest -q tests/test_parsers.py::test_name -k 'search and not slow'",
    "pytest -xvs tests/test_server_tools.py",
    "pytest -ra --tb=short tests",
    "pytest -q --lf",
    "pytest -q -x --maxfail=2 --durations=5",
    "pytest -q -W error::DeprecationWarning",
    "pytest -q --deselect tests/test_parsers.py::test_x",
    "pytest -q -p no:cacheprovider",
    "pytest -pno:cacheprovider -q",
    "pytest --co -q",
    "pytest -k 'a>b' tests",  # a quoted `>` is an argument, not a redirection
    "pytest -q 2>&1 | tail -20",  # a file-descriptor duplication writes nothing
    "pytest tests/test_*.py",  # a glob in a path cannot leave its own directory
    "git diff -- '*.py'",
    "git log --grep=#60 --oneline -5",  # `#` mid-word is not a comment
    "python -m pytest -q",
    "python3 -m pytest tests/test_parsers.py",
    "git status",
    "git status --porcelain",
    "git diff",
    "git diff --stat HEAD~1 HEAD",
    "git diff HEAD~1 -- goodreads_mcp/server.py",
    "git diff --cached",
    "git log --oneline -20",
    "git log -1 -p",
    "git log --format='%s%n%b' -3",
    "git --no-pager log --oneline -5",
    "pytest -q && git status",
    "git diff HEAD~1 | head -40",
    "ls > /tmp/x",  # not a guarded verb
    # --- the corpus pass of #120, the other direction ----------------------
    # The operand rule must leave ordinary revisions and in-tree paths alone:
    # a revision resolves inside the checkout like any relative path does.
    "git diff HEAD~1 HEAD -- goodreads_mcp",
    "git diff main feature -- tests/test_parsers.py",
    "git log -L1,2:goodreads_mcp/server.py",  # an in-tree -L range
    # `-L` carries its path inside the option, where the operand rule does
    # not look -- and does not need to: git refuses a path outside the tree
    # itself ("fatal: '/etc/passwd' is outside repository", git 2.55.0),
    # so the reach the operand rule closes is not open here.
    "git log -L1,2:/etc/passwd",
    # `-c` after the subcommand is git's combined-diff flag and takes no
    # value; the `-c` that loads config stands before the subcommand, where
    # no allow row reaches it (_UNREACHABLE below).
    "git diff -c diff.external=/tmp/prog HEAD",
    "git status goodreads_mcp",
    "git diff ./goodreads_mcp/../tests",  # resolves back inside
    # only `-a` of `set` changes what a child process sees
    "set -e; git diff HEAD",
    "set -o pipefail; pytest -q",
    # `-O` is a letter, not a word: another short option carrying an O in its
    # *value* is not the orderfile option
    "git log --format=%H -1",
    # ... and the letter is only the option where git reads it as one. `-S`
    # takes the rest of its cluster as the pickaxe string, so the O in FOO is
    # text and no order file is opened (git 2.55.0).
    "git diff -SFOO HEAD",
    "git log -pSO/x --oneline -1",
    "git log -S FOO --oneline",
    "git log -GOpen --oneline -5",
    # a word that is an option's value is not an operand: `/foo` here is a
    # ref pattern, and git log exits 0 on it (git 2.55.0)
    "git log --decorate-refs /foo --oneline -1",
    "git log --grep /api/ --oneline -5",
    "echo $HOME",
    "cat <<'EOF'\nit's\nEOF",  # unparseable, but no guarded verb in it
    "",
]


@pytest.mark.parametrize("command,fragment", _DENIED, ids=[c for c, _ in _DENIED])
def test_the_guard_denies(guard, command: str, fragment: str):
    reason = guard.decide(command, _ROOT)
    assert reason is not None, f"{command!r} was let through"
    assert fragment in reason, f"{command!r} was denied for the wrong reason: {reason}"


@pytest.mark.parametrize("command", _PERMITTED, ids=_PERMITTED)
def test_the_guard_stays_silent_on(guard, command: str):
    assert guard.decide(command, _ROOT) is None


def test_the_guard_resolves_paths_against_the_working_directory(guard):
    """`cwd` from the hook payload is where a relative path starts."""
    assert guard.decide("pytest test_parsers.py", _ROOT / "tests") is None
    assert guard.decide("pytest ../goodreads_mcp", _ROOT / "tests") is not None


def _run_guard(payload) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(_GUARD)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_the_script_speaks_the_hook_protocol():
    """Driven the way Claude Code drives it: JSON in, a decision or silence out.

    Silence (exit 0, empty stdout) hands the call to the normal permission
    flow; it never *allows*. A denial is the documented `hookSpecificOutput`
    shape, and the exit code stays 0 so the JSON, not the exit code, decides.
    """
    quiet = _run_guard({"tool_name": "Bash", "cwd": str(_ROOT), "tool_input": {"command": "pytest -q"}})
    assert quiet.returncode == 0 and quiet.stdout == "", quiet

    denied = _run_guard(
        {"tool_name": "Bash", "cwd": str(_ROOT), "tool_input": {"command": "pytest /tmp/proof/notatest.py"}}
    )
    assert denied.returncode == 0, denied
    out = json.loads(denied.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "outside tests/" in out["permissionDecisionReason"]


def test_the_script_fails_closed_on_input_it_cannot_read():
    """A broken payload that mentions a guarded verb is denied, not waved on."""
    broken = _run_guard("not json, but it says pytest")
    assert broken.returncode == 0
    assert json.loads(broken.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    unrelated = _run_guard("")
    assert unrelated.returncode == 0 and unrelated.stdout == ""


# ------------------------------------------------ what the docs claim (#60)


def test_security_ai_records_that_the_prefix_limit_cuts_both_ways():
    """The comfortable version of this paragraph is what hid #60.

    It described only what the *deny* list cannot catch, and concluded that the
    ask on every push is the real gate. The allow side is the sharper edge, and
    a push from inside a process the allow list started is not a tool call.
    """
    text = _SECURITY_AI.read_text(encoding="utf-8")
    for claim in (
        "cuts both ways",
        "allow** side is the sharper edge",
        "as tool calls",
    ):
        assert claim in text, f"docs/SECURITY-AI.md no longer says {claim!r}"


def test_the_claude_readme_no_longer_calls_the_allow_list_read_only():
    """The sentence that hid #60, and the section that replaces it.

    "read-only and routine" was the description; "Know what the allow list
    can't say" is its correction, and "issued as tool calls" is the narrowing
    of "the prompt on every push is the real gate".
    """
    text = _CLAUDE_README.read_text(encoding="utf-8")
    assert "read-only and routine" not in text
    for claim in (
        "Know what the allow list can't say",
        "issued as tool calls",
        "guard-bash.py",
    ):
        assert claim in text, f".claude/README.md no longer says {claim!r}"


def test_security_ai_still_points_at_the_claude_readme():
    """The two documents are one explanation; a dangling pointer splits it."""
    assert "../.claude/README.md" in _SECURITY_AI.read_text(encoding="utf-8")
    assert _CLAUDE_README.is_file()


# --------------------------------------------------------- the corpus (#120)
#
# The tables above grew one spelling at a time, and each fix found the next
# spelling. #120 names the whole corpus once: five families of "something
# outside the part an allow rule matches reaches the tool". The rows live in
# `_DENIED` and `_PERMITTED` above, because those are what actually drive the
# guard; what this section adds is the part a row cannot carry on its own --
# which family it belongs to, whether the family is answered in both
# directions, and which shapes were decided *not* to gate and why.
#
# Two claims are deliberately not made anywhere here, because both are
# asserted in sibling repositories and both are false:
#
# * that a Bash tool call's shell outlives the call. It does not -- verified
#   on Claude Code 2.1.267, `export X=1` in one call and `echo ${X:-UNSET}` in
#   the next printed UNSET. No row below reasons across calls; the export rule
#   is about one command string, where bash really does apply an export to
#   every later command in it.
# * that a leading assignment is part of what an allow rule's prefix matches.
#   The documented rule is the opposite -- an allow rule will not match past
#   an assignment of any variable outside a fixed known-safe set -- so most
#   assignment spellings prompt on their own account. `SAFE_ENV` is a backstop
#   for a set that is not published, not the only thing in front of them.

_ENVIRONMENT = "environment"
_REDIRECTION = "redirection"
_WORD_REWRITING = "word rewriting"
_COMMAND_NAME = "command name"
_OPTIONS = "options"

_FAMILIES = (_ENVIRONMENT, _REDIRECTION, _WORD_REWRITING, _COMMAND_NAME, _OPTIONS)

# One command per family per direction, naming a row that already stands in
# `_DENIED` or `_PERMITTED`. Listing them here rather than tagging the rows
# themselves keeps the driving tables the plain data they are, and makes the
# "is this family answered in both directions" question checkable.
_CORPUS_FAMILIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    _ENVIRONMENT: (
        (
            "GIT_EXTERNAL_DIFF=/tmp/evil.sh git diff HEAD~1 HEAD",
            "GIT_EXTERNAL_DIFF+=/tmp/prog git diff HEAD~1 HEAD",
            "export GIT_EXTERNAL_DIFF; GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD",
            "set -a; GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD",
            "LD_PRELOAD=/tmp/evil.so git status",
        ),
        (
            "GOODREADS_LIVE=1 pytest tests/e2e -v",
            "git diff HEAD; export GIT_EXTERNAL_DIFF=/tmp/prog",
            "set -e; git diff HEAD",
            "export FOO=1; echo hi",
        ),
    ),
    _REDIRECTION: (
        (
            "git diff HEAD~1 >/tmp/d.txt",
            ">goodreads_mcp/server.py git diff HEAD",
            "2>/tmp/err.txt pytest -q",
            "< /etc/passwd pytest -q",
        ),
        (
            "pytest -q 2>&1 | tail -20",
            "ls > /tmp/x",
            "pytest -k 'a>b' tests",
        ),
    ),
    _WORD_REWRITING: (
        (
            "git diff ~/.ssh/id_rsa ~/.bashrc",
            "git diff ~/.env HEAD",
            "pytest ~/evil.py",
            "FOO=$(cat /tmp/x) pytest -q",
        ),
        (
            "git diff -- '*.py'",
            "pytest tests/test_*.py",
            "git log --grep=#60 --oneline -5",
        ),
    ),
    _COMMAND_NAME: (
        (
            "/usr/bin/git diff --no-index .env /etc/hostname",
            "/usr/bin/python3 -m pytest /tmp/evil.py",
            "env GIT_EXTERNAL_DIFF=/tmp/prog git diff HEAD~1 HEAD",
            "exec pytest -q",
        ),
        (
            "python -m pytest -q",
            "git --no-pager log --oneline -5",
            "env FOO=1 echo hi",
        ),
    ),
    _OPTIONS: (
        (
            "git diff .env /etc/hostname",
            "git diff -O/etc/passwd HEAD",
            "git diff --orderfile=/etc/passwd HEAD",
            "git diff -uO/etc/passwd HEAD",
            "git diff -- -/../../etc/hostname .env",
            "pytest -p some_module",
        ),
        (
            "git diff --stat HEAD~1 HEAD",
            "pytest -q -W error::DeprecationWarning",
            "git log -L1,2:goodreads_mcp/server.py",
            "git diff -SFOO HEAD",
            "git log --decorate-refs /foo --oneline -1",
            "pytest -q --lf",
        ),
    ),
}


# Shapes that do reach the tool but that no allow rule here reaches, so the
# guard is not what stands in front of them. Recorded rather than left silent
# (#120 asks for an answer per shape), and checked against the allow list
# rather than asserted in prose, so a rule added to `.claude/settings.json`
# that starts covering one of these fails the row that said it could not.
_UNREACHABLE: tuple[tuple[str, str, str], ...] = (
    (
        _COMMAND_NAME,
        "{,git} diff HEAD --no-index .env /etc/hostname",
        "bash drops the empty alternative as an unquoted null word, so this "
        "really does run `git diff` -- checked against bash 5.3, where "
        "`printf '[%s] ' {,git} diff` prints `[git] [diff]`. What stops it is "
        "the allow list: `Bash(git diff *)` matches a string that begins "
        "`git diff `, and this one begins `{,git}`",
    ),
    (
        _OPTIONS,
        "git -c diff.external=/tmp/prog diff HEAD",
        "the config spelling of GIT_EXTERNAL_DIFF, and it does reach git -- "
        "but a git global option stands before the subcommand, so the string "
        "begins `git -c` and matches no row. `--exec-path` and `--config-env` "
        "are the same shape. A `-c` written *after* the subcommand is git's "
        "combined-diff flag and takes no value",
    ),
)


# Shapes this pass found already filed, and deliberately did not fix here so
# the issue that owns them lands its own change. Pinned to the answer the
# guard gives today, so the row goes red when that issue's fix lands and has
# to be moved into `_DENIED` rather than quietly disagreeing with it.
_TRACKED_ELSEWHERE: tuple[tuple[str, str, str], ...] = (
    (
        _OPTIONS,
        "pytest -W ignore::this.W",
        "#117: `-W` and `--pythonwarnings` are on the guard's safe option "
        "list, and Python parses the value with `warnings._setoption`, which "
        "imports the module of a dotted category before pytest starts. Left "
        "to #117 rather than fixed here, because that issue carries the "
        "verified diff and the value-shape check it needs",
    ),
    (
        _OPTIONS,
        "pytest --pythonwarnings=ignore::this.W",
        "#117, the long spelling of the same option",
    ),
    (
        _OPTIONS,
        "pytest -qW ignore::this.W",
        "#117, the same option in a short cluster",
    ),
)


# Disabling a rule must flip a row of the corpus, or the row is not what holds
# the rule. `(label, before, after, witness)`: the edit is applied to a copy of
# the guard and the witness must stop being denied.
_MUTATIONS: tuple[tuple[str, str, str, str], ...] = (
    (
        "the operand-outside-the-checkout rule",
        "if _is_outside_repo(token, cwd):",
        "if False:",
        "git diff .env /etc/hostname",
    ),
    (
        "`-O`, which is `--orderfile` spelled short",
        "if letter == _GIT_DENIED_SHORT:",
        "if False:",
        "git diff -O/etc/passwd HEAD",
    ),
    (
        "every word after `--` being an operand",
        "after_dashdash = True",
        "after_dashdash = False",
        "git diff -- -/../../etc/hostname .env",
    ),
    (
        "taking redirections out before the verb is looked for",
        "words, redirections = _split_redirections(words)",
        "redirections = []",
        ">goodreads_mcp/server.py git diff HEAD",
    ),
    (
        "reading a command name with its directory taken off",
        'name = words[0].rsplit("/", 1)[-1]',
        "name = words[0]",
        "/usr/bin/git diff --no-index .env /etc/hostname",
    ),
    (
        "an export-family builtin naming a variable with no value",
        'return any(not word.startswith("-") for word in words[1:])',
        "return any(_ASSIGNMENT_RE.match(word) for word in words[1:])",
        "export GIT_EXTERNAL_DIFF; GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD",
    ),
    (
        "`set -a`, which exports every assignment after it",
        "if _turns_on_allexport(words):",
        "if False:",
        "set -a; GIT_EXTERNAL_DIFF=/tmp/prog; git diff HEAD",
    ),
)


# A model of the documented permission matcher, for the unreachability claims
# above. The separators and the wrappers it steps over are documented, and so
# is the assignment rule ("an allow rule won't match past an assignment of any
# other variable"); the known-safe exceptions are left out because they are not
# published, which makes this model decline to match slightly more often than
# the real one -- the safe direction for a claim that nothing reaches.
_MATCHER_SEPARATORS = re.compile(r"&&|\|\||\|&|;|\||&|\n")
_MATCHER_WRAPPERS = frozenset(
    {
        "timeout",
        "time",
        "nice",
        "nohup",
        "stdbuf",
        "command",
        "builtin",
        "noglob",
        "xargs",
    }
)
_MATCHER_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")


def _bash_allow_rules() -> list[str]:
    """Return the `Bash(...)` allow rows, with the wrapper taken off."""

    entries = json.loads(_SETTINGS.read_text(encoding="utf-8"))["permissions"]["allow"]
    return [
        entry[len("Bash(") : -1]
        for entry in entries
        if entry.startswith("Bash(") and entry.endswith(")")
    ]


def _subcommand_matches(part: str, rules: list[str]) -> bool:
    words = part.split()
    while words and words[0] in _MATCHER_WRAPPERS:
        words = words[1:]
    if not words or _MATCHER_ASSIGNMENT.match(words[0]):
        return False
    text = " ".join(words)
    for rule in rules:
        if rule.endswith(" *"):
            prefix = rule[: -len(" *")]
            if text == prefix or text.startswith(f"{prefix} "):
                return True
        elif text == rule:
            return True
    return False


def _matches_an_allow_rule(command: str) -> bool:
    """Whether every subcommand of `command` matches an allow row."""

    rules = _bash_allow_rules()
    parts = [part.strip() for part in _MATCHER_SEPARATORS.split(command)]
    present = [part for part in parts if part]
    return bool(present) and all(_subcommand_matches(part, rules) for part in present)


def test_every_corpus_family_is_answered_in_both_directions():
    """A family answered one way states no rule.

    Without this, a family could be satisfied by "refuse everything", which is
    a guard nobody can use, or by "refuse nothing", which is no guard at all.
    Each listed command has to be a row of the table its direction names, so
    the families cannot drift away from what actually runs.
    """
    assert set(_CORPUS_FAMILIES) == set(_FAMILIES)
    denied = {command for command, _ in _DENIED}
    permitted = set(_PERMITTED)

    for family, (refused, allowed) in _CORPUS_FAMILIES.items():
        assert len(refused) >= 4, f"{family} names {len(refused)} refused shapes"
        assert len(allowed) >= 3, f"{family} names {len(allowed)} allowed shapes"
        missing = [c for c in refused if c not in denied]
        assert not missing, f"{family}: not rows of _DENIED: {missing}"
        missing = [c for c in allowed if c not in permitted]
        assert not missing, f"{family}: not rows of _PERMITTED: {missing}"


def test_the_matcher_model_is_not_vacuous():
    """An unreachability claim from a model that matches nothing is empty."""
    for command in (
        "git diff HEAD",
        "git status",
        "git log --oneline -5",
        "pytest -q",
        "git diff HEAD; git log -1",
    ):
        assert _matches_an_allow_rule(command), (
            f"{command!r} is allow-listed but the model does not match it, so "
            "every unreachability claim it makes is worthless"
        )
    for command in ("curl https://example.com", "rm -rf /"):
        assert not _matches_an_allow_rule(command), (
            f"{command!r} matches no allow row but the model says it does"
        )


@pytest.mark.parametrize(
    ("family", "command", "why"), _UNREACHABLE, ids=[c for _, c, _ in _UNREACHABLE]
)
def test_an_unreachable_shape_matches_no_allow_rule(family, command, why):
    """An unreachable shape is a claim about the allow list; read it there."""
    assert family in _FAMILIES
    assert not _matches_an_allow_rule(command), (
        f"{command!r} now matches an allow rule, so it is reachable and the "
        f"recorded answer is stale: {why}"
    )


@pytest.mark.parametrize(
    ("family", "command", "why"),
    _TRACKED_ELSEWHERE,
    ids=[c for _, c, _ in _TRACKED_ELSEWHERE],
)
def test_a_shape_tracked_elsewhere_still_has_the_answer_it_had(
    guard, family, command, why
):
    """Pinned, not decided, so the owning issue's fix shows up here as a diff."""
    assert family in _FAMILIES
    assert guard.decide(command, _ROOT) is None, (
        f"{command!r} is now denied. That is the fix landing -- move it into "
        f"_DENIED rather than leaving this row disagreeing with it: {why}"
    )


@pytest.mark.parametrize("mutation", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_disabling_a_rule_stops_a_corpus_row_being_denied(mutation, tmp_path):
    """Each new rule must be the one thing that decides its witness.

    A rule with no row depending on it is untested however green the suite is,
    and a row some *other* rule already decides proves nothing about the one it
    was written for.
    """
    label, before, after, witness = mutation
    source = _GUARD.read_text(encoding="utf-8")
    assert source.count(before) == 1, (
        f"the mutation for {label} names {source.count(before)} places in the "
        "guard, so what it disables is not one rule"
    )
    assert witness in {command for command, _ in _DENIED}, (
        f"{witness!r} is not a row of _DENIED, so it witnesses nothing"
    )

    mutant_path = tmp_path / "guard_mutant.py"
    mutant_path.write_text(source.replace(before, after), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("guard_mutant", mutant_path)
    mutant = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mutant)
    # The guard derives the project root from its own location, so a copy in a
    # temporary directory would call every path in the repository "outside the
    # checkout" and deny each witness for a reason that has nothing to do with
    # the rule under test. Point it back at the real root.
    mutant._PROJECT_DIR = _ROOT
    mutant._TESTS_DIR = _ROOT / "tests"

    assert mutant.decide(witness, _ROOT) is None, (
        f"disabling {label} changed nothing: {witness!r} is still denied "
        "without it, so that row does not hold the rule"
    )


# ------------------------------------------ the arity tables, against git
#
# `_GIT_SHORT_VALUED` and `_GIT_VALUE_NEXT` say where git stops reading
# options, and each entry relaxes the guard: a letter listed wrongly lets a
# real `-O` through the cluster scan, and an option listed wrongly lets a real
# outside operand past the operand rule. So every entry is replayed against
# the git on this machine rather than trusted from the comment beside it.

_GIT = shutil.which("git")


@pytest.fixture(scope="module")
def scratch_git(tmp_path_factory):
    """A two-commit repository and a marked file outside it."""
    if _GIT is None:
        pytest.skip("git is not installed")
    base = tmp_path_factory.mktemp("arity")
    repo = base / "repo"
    repo.mkdir()
    outside = base / "outside.txt"
    outside.write_text("OUTSIDE-MARKER\n", encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(base),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [_GIT, "-C", str(repo), *args], capture_output=True, text=True, env=env
        )

    git("init", "-q")
    (repo / "a").write_text("one\n", encoding="utf-8")
    git("add", "a")
    git("commit", "-qm", "one")
    (repo / "a").write_text("two\n", encoding="utf-8")
    git("commit", "-qam", "two")
    return git, outside


# What makes each verb do the work a probe looks for: an order file is only
# read when there is a diff to order.
_PROBE_ARGS = {"git diff": ("diff", "HEAD~1"), "git log": ("log", "-p")}


def test_the_git_probes_can_see_what_they_look_for(scratch_git):
    """A probe that cannot see a read would pass every entry."""
    git, outside = scratch_git
    for args in _PROBE_ARGS.values():
        assert "orderfile" in git(*args, "-pO/nonexistent/order").stderr
    assert "OUTSIDE-MARKER" in git("diff", "--relative", str(outside), "a").stdout
    assert "outside repository" in git("log", str(outside)).stderr


@pytest.mark.parametrize("verb", sorted(_PROBE_ARGS))
def test_a_letter_that_ends_the_cluster_scan_ends_it_in_git(guard, scratch_git, verb):
    git, _ = scratch_git
    opened = [
        letter
        for letter in sorted(guard._GIT_SHORT_VALUED)
        if "orderfile" in git(*_PROBE_ARGS[verb], f"-{letter}O/nonexistent/order").stderr
    ]
    assert not opened, (
        f"`{verb} -<letter>O<path>` opens <path> as an order file for {opened}, "
        "so the -O scan must not stop at them"
    )


@pytest.mark.parametrize("verb", sorted(_PROBE_ARGS))
def test_a_word_skipped_as_a_value_is_a_value_to_git(guard, scratch_git, verb):
    git, outside = scratch_git
    subcommand = _PROBE_ARGS[verb][0]
    operands = []
    for option in sorted(guard._GIT_VALUE_NEXT[verb]):
        alone = git(subcommand, option, str(outside))
        paired = git(subcommand, option, str(outside), "a")
        if (
            "outside repository" in alone.stderr
            or "OUTSIDE-MARKER" in alone.stdout + paired.stdout
        ):
            operands.append(option)
    assert not operands, (
        f"`{verb}` reads the word after {operands} as an operand, so the "
        "operand rule must not skip it"
    )
