#!/usr/bin/env python3
"""PreToolUse guard for the Bash verbs on the allow list. Closes #60.

`.claude/settings.json` allows `pytest`, `git status`, `git diff` and `git log`
without prompting. A permission rule is a prefix match that stops at the verb,
so everything after it is unconstrained -- and three of those verbs reach a
long way past "read-only":

* `pytest <path>` imports whatever the path names, wherever it is, and runs its
  module-level code. Code inside a Python process is not a tool call, so no
  ask or deny rule ever sees what it does. `-p`, `-c`, `--rootdir` and friends
  load code or relocate collection the same way.
* `git diff --no-index <a> <b>` prints any two files on the machine, past the
  `Read(./.env)` deny entry (a Read-tool rule, not consulted for Bash).
* `--output=<path>` on `git diff` or `git log`, and a shell redirection on any
  of the verbs, create or truncate any file the process can write.

No rule can say "bare `pytest`, but not `pytest` with a path", so this hook
does. It is given the whole command string and it denies the spellings above.
Everything else is left alone: the hook prints nothing and exits 0, which
hands the call back to the normal permission flow -- it never *grants*
anything. In particular, the usual invocations still run without a prompt:
`pytest -q`, `pytest tests/test_parsers.py::test_name -k expr`, the coverage
line CI uses, `git diff --stat HEAD~1 HEAD`, `git log --oneline -20`.

How it reads a command: it is tokenised the way a shell would (quotes respected;
`;`, `&&`, `||`, `|`, `&`, `(`, `)` and newlines split it into simple
commands), and each simple command whose verb is guarded is checked on its own.
Anything the guard cannot see through -- `$var`, `$(...)`, a backtick, a brace
expansion, a glob standing where an option goes, a string that does not
tokenise -- is denied rather than guessed at, but only in a guarded command;
`echo $HOME` is not this hook's business.

The guard only holds while the string it reads is the string the shell runs.
Two ways that used to come apart, both of them a bypass:

* `#` starts a comment in `shlex` wherever it appears, but in a shell only at
  the start of a word. `pytest --ignore=z#z /tmp/evil.py` reached the guard as
  `pytest --ignore=z` -- clean -- and reached pytest whole. The lexer is given
  no comment character now, so nothing is dropped; a real trailing comment is
  read as arguments and refused, which is the safe direction to be wrong in.
* Brace expansion happens after the guard has looked and spells a denied flag
  out of tokens that do not contain it: `git diff --no-inde{x,x} a b` is
  `git diff --no-index --no-index a b` by the time git sees it. Braces are
  refused rather than expanded here, because an expander that disagreed with
  the shell in the other direction would be this same bug again.

Exercised by `tests/test_agent_permissions.py`. If you change what is denied
here, change the tables there.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

# The project root is two levels up from this file; `CLAUDE_PROJECT_DIR` is the
# same directory when Claude Code runs the hook, but the file location is what
# the tests rely on, so it is the primary source.
_PROJECT_DIR = Path(__file__).resolve().parents[2]
_TESTS_DIR = _PROJECT_DIR / "tests"

_GIT_VERBS = {"status", "diff", "log"}

# What this hook covers, spelled the way the allow list spells its Bash verbs.
# `tests/test_agent_permissions.py` asserts every `Bash(...)` allow entry is in
# this set, so a new verb cannot join the allow list without a decision here.
GUARDED = {"pytest", *(f"git {verb}" for verb in _GIT_VERBS)}

# Interpreter spellings for `python -m pytest`.
_PYTHON_RE = re.compile(r"^python(3(\.\d+)?)?$")

# Environment assignments in front of the verb that change what pytest or the
# interpreter loads. `GOODREADS_LIVE=1 pytest tests/e2e -v` is fine.
_UNSAFE_ENV = {
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "PYTHONEXECUTABLE",
}

# pytest options the guard lets through. The value is whether the option takes
# an argument, which matters for telling `-k expr` apart from a path. Anything
# not listed is denied, so a new option has to be added here on purpose; the
# ones deliberately absent load code (`-p`, `-c`, `--pdbcls`, `--pyargs`,
# `--doctest-modules`), relocate collection (`--rootdir`, `--confcutdir`,
# `--import-mode`, `-o`/`--override-ini`), or write files (`--basetemp`, which
# also deletes, `--junitxml`, `--log-file`, `--debug`).
_PYTEST_SHORT = {
    "q": False,
    "v": False,
    "x": False,
    "s": False,
    "l": False,
    "h": False,
    "k": True,
    "m": True,
    "r": True,
    "W": True,
}
_PYTEST_LONG = {
    "--quiet": False,
    "--verbose": False,
    "--exitfirst": False,
    "--capture": True,
    "--showlocals": False,
    "--no-showlocals": False,
    "--help": False,
    "--version": False,
    "--keyword": True,
    "--markers": False,
    "--fixtures": False,
    "--fixtures-per-test": False,
    "--strict-markers": False,
    "--strict-config": False,
    "--strict": False,
    "--tb": True,
    "--full-trace": False,
    "--color": True,
    "--code-highlight": True,
    "--maxfail": True,
    "--durations": True,
    "--durations-min": True,
    "--lf": False,
    "--last-failed": False,
    "--ff": False,
    "--failed-first": False,
    "--nf": False,
    "--new-first": False,
    "--sw": False,
    "--stepwise": False,
    "--sw-skip": False,
    "--stepwise-skip": False,
    "--lfnf": True,
    "--last-failed-no-failures": True,
    "--cache-clear": False,
    "--cache-show": False,
    "--co": False,
    "--collect-only": False,
    "--continue-on-collection-errors": False,
    "--deselect": True,
    "--ignore": True,
    "--ignore-glob": True,
    "--runxfail": False,
    "--no-header": False,
    "--no-summary": False,
    "--no-fold-skipped": False,
    "--disable-warnings": False,
    "--disable-pytest-warnings": False,
    "--pythonwarnings": True,
    "--setup-only": False,
    "--setup-show": False,
    "--setup-plan": False,
    "--timeout": True,
    # pytest-cov, as CI invokes it. `--cov-report` is checked separately: only
    # the terminal reporters are allowed, because `html:` and `xml:` name a
    # path to write.
    "--cov": True,
    "--cov-report": True,
    "--cov-fail-under": True,
    "--cov-branch": False,
    "--no-cov": False,
    "--no-cov-on-fail": False,
    "--cov-append": False,
    "--cov-reset": False,
}
_COV_REPORT_RE = re.compile(r"^(term|term-missing)(:skip-covered)?$|^$")

# `git diff` and `git log` options that read or write outside the repository.
# Git rejects every abbreviation of these (`--no-ind`, `--outp`, ...), so exact
# matching is enough.
_GIT_DENIED = {"--no-index", "--output", "--output-file"}

# Shell operator characters. Any token made only of these is an operator; the
# ones with `<` or `>` are redirections, the rest separate simple commands.
_PUNCTUATION = "();<>|&\n"
_FD_DUP = {">&", "<&"}

# Characters that make a token stand for something other than itself by the
# time the shell has finished with it: `$` and a backtick substitute unknown
# text, and `{` `}` brace-expand, which assembles a denied flag out of a token
# that does not contain one.
_OPAQUE = ("$", "`", "{", "}")

# Glob metacharacters. A path may carry them -- `pytest tests/test_*.py`,
# `git diff -- '*.py'` -- and a glob cannot walk a path out of the directory
# its literal prefix names, so paths are left alone. An option never carries
# them, and a glob standing where an option goes matches whatever the
# filesystem happens to hold, including a file named after a denied flag.
_GLOB = ("*", "?", "[", "]")


class Denied(Exception):
    """Raised with the reason a command is refused."""


# ---------------------------------------------------------------- tokenising


def _tokenise(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace = " \t\r"  # newline is a separator, not whitespace
    lexer.whitespace_split = True
    # `shlex` treats `#` as a comment anywhere, a shell only at the start of a
    # word. Dropping the rest of `--ignore=z#z /tmp/evil.py` would hide from
    # the guard exactly the part the shell goes on to run.
    lexer.commenters = ""
    return list(lexer)


def _is_operator(token: str) -> bool:
    return bool(token) and all(ch in _PUNCTUATION for ch in token)


def _is_redirection(token: str) -> bool:
    """Any operator with `<` or `>` in it, so `>|` and `&>` count as one too."""
    return "<" in token or ">" in token


def _simple_commands(tokens: list[str]) -> list[list[str]]:
    """Split on separators. Redirection operators stay in their command."""
    commands: list[list[str]] = [[]]
    for token in tokens:
        if _is_operator(token) and not _is_redirection(token):
            commands.append([])
            continue
        commands[-1].append(token)
    return [c for c in commands if c]


def _strip_redirections(words: list[str], verb: str) -> list[str]:
    """Deny any redirection except a file-descriptor duplication (`2>&1`)."""
    out: list[str] = []
    i = 0
    while i < len(words):
        token = words[i]
        if _is_operator(token):
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if token in _FD_DUP and (nxt.isdigit() or nxt == "-"):
                if out and out[-1].isdigit():
                    out.pop()  # the `2` of `2>&1`
                i += 2
                continue
            raise Denied(
                f"`{verb}` with a shell redirection (`{token}`) reads or writes "
                "a file of the shell's choosing; the allow list never meant to "
                "grant that"
            )
        out.append(token)
        i += 1
    return out


# ------------------------------------------------------------------- pytest


def _check_pytest(args: list[str], cwd: Path) -> None:
    positional: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            positional.extend(args[i + 1 :])
            break
        if token.startswith("--"):
            name, eq, value = token.partition("=")
            if name not in _PYTEST_LONG:
                raise Denied(
                    f"pytest option `{name}` is not on the guard's safe list "
                    "(.claude/hooks/guard-bash.py); options that load code, "
                    "relocate collection or write files are refused"
                )
            if _PYTEST_LONG[name] and not eq:
                i += 1
                value = args[i] if i < len(args) else ""
            if name == "--cov-report" and not _COV_REPORT_RE.match(value):
                raise Denied(
                    f"`--cov-report={value}` writes files; only the terminal "
                    "reporters (`term`, `term-missing`) are allowed"
                )
            i += 1
            continue
        if token.startswith("-") and len(token) > 1:
            # A cluster of short flags (`-xvs`), possibly ending in one that
            # takes a value (`-kexpr`, or `-k expr` if nothing follows).
            rest = token[1:]
            while rest:
                flag, rest = rest[0], rest[1:]
                if flag == "p":
                    # `-p no:name` disables a plugin and loads nothing; any
                    # other value names a module to import.
                    value = rest or (args[i + 1] if i + 1 < len(args) else "")
                    if not value.startswith("no:"):
                        raise Denied(
                            f"`-p {value}` imports a module as a pytest plugin"
                        )
                    if not rest:
                        i += 1
                    break
                if flag not in _PYTEST_SHORT:
                    raise Denied(
                        f"pytest option `-{flag}` is not on the guard's safe "
                        "list (.claude/hooks/guard-bash.py); options that load "
                        "code, relocate collection or write files are refused"
                    )
                if _PYTEST_SHORT[flag]:
                    if not rest:
                        i += 1  # the value is the next token
                    break
            i += 1
            continue
        positional.append(token)
        i += 1

    for path in positional:
        target = path.split("::", 1)[0]
        resolved = (cwd / target).resolve()
        if resolved != _TESTS_DIR and _TESTS_DIR not in resolved.parents:
            raise Denied(
                f"pytest would collect `{path}`, which is outside tests/; "
                "pytest imports what it collects, so a path outside the suite "
                "is arbitrary code execution"
            )


# ---------------------------------------------------------------------- git


def _check_git(verb: str, args: list[str]) -> None:
    for token in args:
        name = token.partition("=")[0]
        if name in _GIT_DENIED:
            raise Denied(
                f"`{verb} {name}` reaches outside the repository "
                "(`--no-index` reads any file, `--output` writes one)"
            )


# --------------------------------------------------------------------- driver


def _check_command(words: list[str], cwd: Path) -> None:
    # Leading VAR=value assignments.
    env: list[str] = []
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        env.append(words.pop(0))
    if not words:
        return

    head = words[0]
    if _PYTHON_RE.match(head) and words[1:3] == ["-m", "pytest"]:
        verb, args = "pytest", words[3:]
    elif head == "pytest":
        verb, args = "pytest", words[1:]
    elif head == "git":
        # `git --no-pager diff` and the like: the verb is the first token that is
        # not an option. A global option with a value (`-C dir`) makes its value
        # look like the verb; that is harmless, because the allow list only
        # matches `git <verb>` directly and such a command prompts anyway.
        rest = [w for w in words[1:] if not w.startswith("-")]
        if not rest or rest[0] not in _GIT_VERBS:
            return
        verb = rest[0]
        args = words[words.index(verb) + 1 :]
    else:
        return

    if verb != "pytest":
        verb = f"git {verb}"

    for token in words:
        opaque = next((ch for ch in _OPAQUE if ch in token), None)
        if opaque is not None:
            raise Denied(
                f"`{verb}` with `{opaque}` in it: the guard cannot see what the "
                "shell would substitute or expand, so it cannot check the "
                "command that would actually run"
            )
        if token.startswith("-") and any(ch in token for ch in _GLOB):
            raise Denied(
                f"`{verb}` with a glob in an option (`{token}`): what it "
                "expands to depends on what is on disk, so the guard cannot "
                "see which option this is"
            )

    args = _strip_redirections(args, verb)

    if verb == "pytest":
        for assignment in env:
            name = assignment.partition("=")[0]
            if name in _UNSAFE_ENV:
                raise Denied(
                    f"`{name}=...` in front of pytest changes what it loads"
                )
        _check_pytest(args, cwd)
    else:
        _check_git(verb, args)


def decide(command: str, cwd: str | os.PathLike[str] | None = None) -> str | None:
    """Return the reason the command is denied, or None to leave it alone."""
    cwd_path = Path(cwd) if cwd else _PROJECT_DIR
    try:
        tokens = _tokenise(command)
    except ValueError as exc:
        if re.search(r"\b(pytest|git)\b", command):
            return f"the guard could not parse this command ({exc}); rephrase it"
        return None
    try:
        for words in _simple_commands(tokens):
            _check_command(words, cwd_path)
    except Denied as exc:
        return str(exc)
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
        command = payload.get("tool_input", {}).get("command", "")
        reason = decide(command, payload.get("cwd"))
    except Exception as exc:  # noqa: BLE001 - a broken guard must fail closed
        command = raw
        reason = f"guard-bash.py failed ({exc!r}); fix the hook before relying on it"
        if not re.search(r"\b(pytest|git)\b", raw):
            reason = None
    if reason is None:
        return 0
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
