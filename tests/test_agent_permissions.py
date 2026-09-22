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
