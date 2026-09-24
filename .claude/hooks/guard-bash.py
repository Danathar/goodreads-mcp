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
`echo $HOME` is not this hook's business. The exception is a command
substitution, which is checked against the whole string rather than one
command, because it is the construct that moves words between them: see
`_SUBSTITUTION_RE`.

The guard only holds while the string it reads is the string the shell runs.
Six ways that used to come apart, all of them a bypass:

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
* A `VAR=value` assignment in front of the verb was popped off before any of
  the above ran, so neither half of it was read: not the value, which is shell
  text like any other word (`FOO=$(...) pytest -q` substituted unseen), and not
  the name, unless the verb happened to be pytest -- so
  `GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD` ran `prog` once per changed
  path. The shell applies an assignment to the process the allow list started,
  which makes it part of the command; it is read with the rest of the command
  now, against a safe list of names rather than a list of dangerous ones (#115).
  Three spellings of the same assignment go with it: bash's `NAME+=value`
  append form, which creates the variable when it is unset; the export family
  (`export NAME=value`, `declare -x`, `typeset -x`, `readonly`), which bash
  applies to every command it runs later in the same string; and a wrapper
  (`env`, `command`, `timeout`, and `env -S` above all), whose own options this
  guard does not model and which is therefore denied in front of a guarded
  verb rather than guessed at. Two more went in with the whole-corpus pass
  (#120): a bare `export NAME` that a later command assigns to, and `set -a`,
  which exports every assignment after it without naming a builtin at all.
* zsh's `noglob` is a wrapper Claude Code's permission matcher steps over, and
  the guard did not know it, so `noglob pytest -p evil` reached
  `Bash(pytest *)` unchecked. A bare `noglob` takes no options, so the command
  after it is checked in its place; a path spelling (`/usr/bin/noglob`) is some
  other program and is denied like the wrappers above.
* An operand naming a path outside the checkout is `--no-index` with no option
  written. Given two paths and at least one outside the working tree,
  `git diff` prints both files whole -- verified against git 2.55.0, where
  `git diff .env /etc/hostname` printed the .env that `Read(./.env)` exists to
  withhold. The string begins `git diff `, so `Bash(git diff *)` approves it
  and there is no option on the line for the denied-option list to match. A
  leading `~` counts as outside whatever `HOME` is set to, because bash
  expands it before git runs.
* A redirection may stand in front of the command name -- bash reads
  `>out git diff HEAD` and `git diff HEAD >out` as the same command. The
  operator used to be read as the command's name, so the guard fell through
  its dispatch and charged the write to nothing.

Four shapes of the corpus are decided as *not reachable* rather than refused,
so a later pass does not work them out again. Each rests on the allow list,
not on a guess about what is dangerous:

* A git global option before the subcommand -- `git -c diff.external=prog
  diff`, `--exec-path`, `--config-env`. These do reach git, and `-c
  diff.external=` is the config spelling of `GIT_EXTERNAL_DIFF`. What stops
  them is that `Bash(git diff *)` matches a string beginning `git diff `, and
  a global option puts `git -c` at the front instead.
* A brace-expanded command name, `{,git} diff`. bash drops the empty
  alternative as an unquoted null word, so this really does run git (checked
  against bash 5.3); the string begins `{,git}` and matches no row.
* `python -m pytest`, which the guard checks anyway. No row covers it --
  `Bash(pytest *)` wants a string beginning `pytest ` -- so it prompts on its
  own account; it is read here because a guard that recognised one spelling of
  an interpreter and not another would be deciding by accident.
* A redirection written after a subshell or a brace group,
  `(git diff HEAD) >README.md` or `{ git log --stdin; } <.env` (#144). bash
  applies it to the verb inside, but the guard charges it to no verb, because
  `(`, `)` and the `;` before `}` end the simple command it belongs to. Claude
  Code asks before it runs any command that contains a subshell or a brace
  group, whatever the allow rows say (checked on 2.1.273 and 2.1.280). No row
  here names one, and `tests/test_agent_permissions.py` fails if one is added.

Nothing here reasons across Bash tool calls. A call does not inherit the
previous call's environment (verified on Claude Code 2.1.267: `export X=1` in
one call, `echo ${X:-UNSET}` in the next, prints UNSET), so the export rule is
about one command string -- which is where bash really does apply an export to
every later command. Where a spelling prompts rather than being refused here,
the reason is Claude Code's own matcher: an allow rule has to match each
subcommand of a string independently, across `;`, `&&`, `||`, `|`, `|&`, `&`
and a newline.

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

# An assignment as bash's grammar spells it, which is wider than `NAME=value`.
# `NAME+=value` appends and *creates* the variable when it is unset, so
# `GIT_EXTERNAL_DIFF+=prog git diff HEAD~1 HEAD` is the same environment as the
# plain form; the `^[A-Za-z_][A-Za-z0-9_]*=` this replaces did not match it, so
# the word went on to be read as the command's name and the guard returned
# without checking anything. Group 1 is the variable, without the `+`.
_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?\+?=")

# `export NAME=value`, and the three builtins that spell the same thing with a
# flag. What they set is in the environment of every command bash runs later in
# the same string, so the assignment reaches a guarded verb that carries none
# of its own: `export GIT_EXTERNAL_DIFF=prog; git diff HEAD~1 HEAD` ran prog
# once per changed path while the git command looked bare. The builtin is
# refused rather than its options read, because the flag that exports has
# several spellings (-x, -gx, a plain `NAME=value` after an earlier
# `declare -x NAME`) and a half-read option list is a guard that disagrees with
# bash in some other direction.
_EXPORT_BUILTINS = frozenset({"export", "declare", "typeset", "readonly"})

# Words that run another command and whose own options this guard does not
# model. `env GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD` put the assignment
# behind a word the guard read as the verb, so it returned at the first `if`.
# `env -S` goes further and hides the whole invocation inside one word. Like
# every other construct the guard cannot see through, a wrapper standing in
# front of a guarded verb is denied rather than guessed at.
#
# The list covers every wrapper Claude Code's permission matcher steps over
# before it compares a command with an allow row (timeout, time, nice, stdbuf,
# nohup, command, builtin, noglob, and xargs through its `xargs <prefix>`
# case), because each of those reaches `Bash(pytest *)` or `Bash(git diff *)`
# with no prompt. The matcher compares what follows the word's last `/` or
# `\`, so `/usr/bin/noglob pytest -p evil` and `'./shim\nohup' git diff HEAD`
# are approved as readily as the bare spelling. A `/` spelling is read below
# with its directory taken off; a `\` spelling is refused on the string as
# typed (`_backslash_wrapper`), because bash takes an unquoted `\` out.
# `noglob` was missing: `noglob pytest -p evil` and
# `noglob git diff --no-index /dev/null ./.env` passed this guard, and matched
# the allow rows. A bare `noglob` is zsh's precommand modifier, which takes no
# options and turns off globbing for the command after it, so that command is
# checked in its place (`_check_command` steps over it). Only a path spelling
# reaches this list: `/usr/bin/noglob` or `$HOME/bin/noglob` is some other
# program the matcher reads as the modifier, and the guard cannot see what it
# runs. In bash a bare `noglob` is not a command, but bash has already applied
# any redirection by then, and the step-over checks that too.
_WRAPPERS = frozenset(
    {
        "env",
        "command",
        "exec",
        "time",
        "builtin",
        "nohup",
        "nice",
        "timeout",
        "noglob",
        "stdbuf",
        "xargs",
    }
)

# Environment assignments in front of the verb. The shell applies them to the
# process the allow list started, so they are part of the command, not a detail
# of it -- and several reach further than anything the option tables refuse:
# `GIT_EXTERNAL_DIFF` names a program git runs once per changed path,
# `PYTHONWARNINGS` imports its category's module at interpreter startup, and
# `LD_PRELOAD` runs an object in the process before Python starts.
#
# This is an allow list for the same reason the pytest options are: a deny list
# of the names thought of so far admits every name nobody thought of, and the
# three above were all outside the one that stood here (#115). A variable has to
# be added on purpose, and only the ones below change nothing about what the
# command loads or runs.
SAFE_ENV = {
    "GOODREADS_LIVE",  # the live suite's switch: GOODREADS_LIVE=1 pytest tests/e2e
    "GOODREADS_USER_ID",  # the shelf tools' default id, for a live run
    "CI",
    "TZ",
    "LANG",
    "LC_ALL",
    "NO_COLOR",
    "FORCE_COLOR",
    "PY_COLORS",
}

# pytest options the guard lets through. The value is whether the option takes
# an argument, which matters for telling `-k expr` apart from a path. Anything
# not listed is denied, so a new option has to be added here on purpose; the
# ones deliberately absent load code (`-p`, `-c`, `--pdbcls`, `--pyargs`,
# `--doctest-modules`), relocate collection (`--rootdir`, `--confcutdir`,
# `--import-mode`, `-o`/`--override-ini`), or write files (`--basetemp`, which
# also deletes, `--junitxml`, `--log-file`, `--debug`). Two listed options are
# safe only for some values, and their values are checked: `--cov-report`
# (terminal reporters only) and `-W`/`--pythonwarnings` (no dotted category).
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


def _check_warning_filter(value: str) -> None:
    """Refuse a `-W` value whose warning category is a dotted name (#117).

    pytest resolves the category of `action:message:category:module:lineno`
    by importing the module of a dotted name before a test is collected:
    `-W ignore::this.W` imports `this` and runs its top-level code, the reach
    `-p` is refused for. A built-in category has no dot, and that is all this
    repository passes (`-W error::DeprecationWarning`).
    """
    parts = value.split(":")
    category = parts[2].strip() if len(parts) > 2 else ""
    if "." in category:
        raise Denied(
            f"`-W {value}` names the warning category `{category}`, and pytest "
            "imports the module of a dotted category before it runs anything; "
            "only a built-in category (no dot) is allowed"
        )


# `git diff` and `git log` options that read or write outside the repository.
# Git rejects every abbreviation of these, so exact matching is enough: checked
# against git 2.55.0, where `--no-inde`, `--outpu` and `--outp` are all
# "ambiguous option" (`--no-indent-heuristic` and `--output-indicator-context`
# stand next to them) and `--orderfil` is "unknown option". `--output` is worth
# the entry even though git then exits 129 on the separate-value form, because
# it creates the file before it complains: `git diff --output /tmp/x HEAD`
# leaves /tmp/x truncated and exits non-zero.
_GIT_DENIED = {"--no-index", "--output", "--output-file", "--orderfile"}

# `-O<file>` is `--orderfile` spelled short, and git takes it clustered behind
# other short options (`-pO/etc/passwd` runs; only the value has to be attached
# to the letter), so the letter is looked for all through a cluster, up to the
# first letter whose value is the rest of it, rather than only at its head.
# It makes git open a path of the caller's choosing to read sort patterns out
# of it. That is a narrower reach than the entries above --
# the file orders the diff and is never printed, so it discloses nothing by
# itself -- but it is the same kind of reach, an option naming a path outside
# the repository, and it belongs with them rather than in a second category.
# Decided in #125 to keep it refused: refusing it costs nothing.
_GIT_DENIED_SHORT = "O"

# Short options whose value is the rest of their cluster. Git stops reading a
# cluster as options at the first of these, so the `-O` scan stops there too:
# `-SFOO` searches for FOO and `-pSO/x` searches for `O/x`, and neither opens
# an order file. Checked against git 2.55.0 in `git diff` and `git log` --
# `-<letter>O<path>` never read <path> as an order file for any letter here --
# and replayed against real git by tests/test_agent_permissions.py. A letter
# that is a flag in either verb (`-u` is `--patch` in diff) must not be here,
# or `-uO<path>` would read the path past a scan that stopped at it.
_GIT_SHORT_VALUED = frozenset("SGIlnUMCBXL")

# Options that take the next word as their value when none is attached. That
# word is a value, not an operand, so the outside-the-checkout rule skips it:
# `git log --decorate-refs /foo` names a ref pattern, not a path. Only options
# whose value is required belong here. `--relative`, `--stat`, `-U` and the
# other optional-value options take an attached value only, and the next word
# stays an operand -- `git diff --relative <outside> <inside>` prints the
# outside file. Per verb, because `-L` and `--decorate-refs` are log options
# and `git diff` reads the word after them as a path. Checked against git
# 2.55.0 and replayed against real git by tests/test_agent_permissions.py.
_GIT_VALUE_NEXT_DIFF = frozenset(
    {
        "-S",
        "-G",
        "-I",
        "--author",
        "--committer",
        "--grep",
        "--anchored",
        "--find-object",
        "--ignore-matching-lines",
        "--line-prefix",
        "--src-prefix",
        "--dst-prefix",
        "--rotate-to",
        "--skip-to",
        "--word-diff-regex",
    }
)
_GIT_VALUE_NEXT = {
    "git diff": _GIT_VALUE_NEXT_DIFF,
    "git log": _GIT_VALUE_NEXT_DIFF
    | {"-L", "--decorate-refs", "--decorate-refs-exclude"},
    "git status": frozenset(),
}

# Shell operator characters. Any token made only of these is an operator; the
# ones with `<` or `>` are redirections, the rest separate simple commands.
_PUNCTUATION = "();<>|&\n"
_FD_DUP = {">&", "<&"}

# Characters that make a token stand for something other than itself by the
# time the shell has finished with it: `$` and a backtick substitute unknown
# text, and `{` `}` brace-expand, which assembles a denied flag out of a token
# that does not contain one.
_OPAQUE = ("$", "`", "{", "}")

# A command substitution is the one opaque construct that does not stay inside
# the token it starts in: `$(` puts a `(` in the stream, which splits simple
# commands here but not in a shell, and a backtick leaves the words between the
# pair standing where the verb or its arguments go. Either way the token the
# scan above would have caught ends up in a different simple command from the
# guarded verb -- `FOO=$(...) pytest -q` reached the guard as a bare
# `pytest -q`. So the whole string is checked for one, whenever a guarded verb
# is anywhere in it.
_SUBSTITUTION_RE = re.compile(r"\$\(|`")
_GUARDED_WORD_RE = re.compile(r"\b(pytest|git)\b")

# A word as typed, before the shell takes quotes and backslashes out of it.
# Claude Code's matcher finds a wrapper in the text as typed, cutting the
# command word at its last `/` or `\`, and bash does not: an unquoted `\`
# escapes the next character and goes away, so `/usr/bin\timeout 5 pytest -q
# >out` is the file `/usr/bintimeout` to bash -- not found, exit 127, but `out`
# is truncated first -- while the matcher steps over `timeout` and matches
# `pytest *`. The lexer has already dropped the backslash, so the word it hands
# on is `/usr/bintimeout`, no wrapper and no guarded verb. The command word is
# looked for in the words as typed instead. Only the command word: the matcher
# steps over wrappers there and nowhere else, so `git log --grep='x\nohup'` is
# an ordinary argument.


def _raw_tokens(command: str) -> list[str]:
    """Split the way `_tokenise` does, keeping each word as typed.

    Quotes and backslashes stay in the word; a quoted or escaped character
    does not end it or start an operator.
    """
    tokens: list[str] = []
    word: list[str] = []
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            word.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                i += 1
                word.append(command[i])
            elif ch == quote:
                quote = ""
        elif ch == "\\":
            word.append(command[i : i + 2])
            i += 1
        elif ch in "'\"":
            quote = ch
            word.append(ch)
        elif ch in " \t\r" or ch in _PUNCTUATION:
            if word:
                tokens.append("".join(word))
                word = []
            if ch in _PUNCTUATION:
                if tokens and _is_operator(tokens[-1]) and command[i - 1] == tokens[-1][-1]:
                    tokens[-1] += ch
                else:
                    tokens.append(ch)
        else:
            word.append(ch)
        i += 1
    if word:
        tokens.append("".join(word))
    return tokens


def _backslash_wrapper(command: str) -> str | None:
    """Return a command word as typed that has a `\\` and reads as a wrapper.

    Only where a guarded word is in the same simple command.
    """
    for words in _simple_commands(_raw_tokens(command)):
        words, _ = _split_redirections(words)
        while words and (words[0] == "noglob" or _ASSIGNMENT_RE.match(words[0])):
            words.pop(0)
        if (
            words
            and "\\" in words[0]
            and re.split(r"[\\/]", words[0].strip("'\""))[-1] in _WRAPPERS
            and _GUARDED_WORD_RE.search(" ".join(words))
        ):
            return words[0]
    return None


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


def _split_redirections(words: list[str]) -> tuple[list[str], list[str]]:
    """Separate a simple command's words from the redirections written in it.

    Returned rather than refused on the spot, because the reason names the
    verb and the verb is not known until the words are read -- which is the
    bug this replaced. A redirection may stand anywhere in a simple command,
    the command name included: bash reads `>out git diff HEAD` and
    `git diff HEAD >out` as the same command. The operator used to be left
    where it was and the words scanned from the front, so a leading one was
    read as the command's name, `_check_command` fell through its `else` and
    returned, and the redirection was never charged to anything.

    A file-descriptor duplication (`2>&1`, `>&-`) names no path and is dropped
    along with its operand; every other form is collected, spelled the way it
    was written so the refusal can quote it.
    """

    out: list[str] = []
    found: list[str] = []
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
            descriptor = out.pop() if out and out[-1].isdigit() else ""
            found.append(f"{descriptor}{token}{nxt}")
            i += 2
            continue
        out.append(token)
        i += 1
    return out, found


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
            if name == "--pythonwarnings":
                _check_warning_filter(value)
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
                    if flag == "W":
                        _check_warning_filter(
                            rest or (args[i + 1] if i + 1 < len(args) else "")
                        )
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


def _is_outside_repo(word: str, cwd: Path) -> bool:
    """Whether an operand of a git command names a path outside the checkout.

    The caller decides which words are operands. A leading `~` is outside by
    definition, because bash expands it to a home directory before git runs
    and never to a path under this checkout, so the answer must not depend on
    where `HOME` points or whether it is set.
    """

    if word.startswith("~"):
        return True
    try:
        return not (cwd / word).resolve().is_relative_to(_PROJECT_DIR)
    except (ValueError, RuntimeError, OSError):
        return True


def _names_an_orderfile(token: str) -> bool:
    """Whether a short-option cluster holds `-O`, read the way git reads it.

    Letters are options until the first one whose value is the rest of the
    cluster; an `O` inside that value is text. `git diff -SFOO HEAD` searches
    for FOO and opens no order file.
    """
    if not token.startswith("-") or token.startswith("--"):
        return False
    for letter in token[1:]:
        if letter == _GIT_DENIED_SHORT:
            return True
        if letter in _GIT_SHORT_VALUED:
            return False
    return False


def _check_git(verb: str, args: list[str], cwd: Path) -> None:
    takes_value = _GIT_VALUE_NEXT[verb]
    after_dashdash = False
    value_next = False
    # The two name checks run on every word, a value included: a value that
    # happens to spell a denied option is refused, which costs nothing, and
    # an entry wrongly in `_GIT_VALUE_NEXT` then cannot hide one.
    for token in args:
        name = token.partition("=")[0]
        if name in _GIT_DENIED:
            raise Denied(
                f"`{verb} {name}` reaches outside the repository "
                "(`--no-index` reads any file, `--output` writes one)"
            )
        if _names_an_orderfile(token):
            raise Denied(
                f"`{verb} {token}` reaches outside the repository: `-O` is "
                "`--orderfile` spelled short, and git reads the path attached "
                "to it"
            )
        if value_next:
            # `--grep /x`: the option's value, which git never reads as a path.
            value_next = False
            continue
        if not after_dashdash:
            if token == "--":
                after_dashdash = True
                continue
            if token.startswith("-"):
                value_next = token in takes_value
                continue
        # Every word after `--` is an operand, whatever it begins with: with a
        # directory named `-` in the checkout, `git diff -- -/../../x .env`
        # prints x (git 2.55.0).
        #
        # Decided in #125: the rule is wide. Any operand outside the checkout
        # is refused on all three verbs, not only the two-path form that makes
        # `git diff` go `--no-index`. It is simpler to state and errs the safe
        # way. What it costs is `git diff ../other-repo/file`, which is rare,
        # and a person can run it.
        if _is_outside_repo(token, cwd):
            raise Denied(
                f"`{verb} {token}` names a path outside the repository, which "
                "is `--no-index` without the option: given two paths with at "
                "least one outside the working tree, `git diff` prints both "
                "files whole and no option appears on the command line for "
                "the denied-option list above to match. Verified against git "
                "2.55.0 -- `git diff .env /etc/hostname` printed `.env`, which "
                "`Read(./.env)` exists to withhold, and the string starts "
                "`git diff ` so `Bash(git diff *)` approves it with no prompt. "
                "Read a file with the Read tool, which the deny list governs"
            )


# --------------------------------------------------------------------- driver


def _check_command(words: list[str], cwd: Path) -> None:
    # Redirections first: bash lets one stand in front of the command name, so
    # taking them out is what makes the word after them the verb rather than
    # the `>` being read as the verb and the command falling through unchecked.
    words, redirections = _split_redirections(words)

    # Leading VAR=value assignments, and zsh's `noglob` modifier among them.
    # A bare `noglob` takes no options and runs the next word as the command,
    # so that command is the one to check: `noglob echo git` is `echo git`, not
    # a wrapper hiding a guarded verb. zsh does not read an assignment after it
    # as one (`noglob FOO=1 cmd` looks for a command named `FOO=1`), but it is
    # read as one here anyway -- checked against the safe list, the only cost
    # is refusing a command zsh could not find.
    env: list[str] = []
    while words and (words[0] == "noglob" or _ASSIGNMENT_RE.match(words[0])):
        word = words.pop(0)
        if word != "noglob":
            env.append(word)
    if not words:
        return

    # `/usr/bin/git` and `./pytest` run the same tools their bare names do, so
    # the name is read with any directory taken off. The allow list matches the
    # bare spelling only, so a qualified one prompts rather than reaching a
    # rule -- it is read here because a guard that recognised one spelling and
    # not the other would be deciding by accident.
    name = words[0].rsplit("/", 1)[-1]

    # A wrapper the guard cannot see through, in front of something it guards.
    # A wrapper spelled with a `\` never gets this far: `decide` refuses it on
    # the string as typed, since an unquoted `\` is gone from `words` by now.
    if name in _WRAPPERS and _GUARDED_WORD_RE.search(" ".join(words)):
        raise Denied(
            f"`{words[0]}` runs another command and this guard does not model "
            f"its options, so it cannot tell what `{words[0]}` would run or "
            "what environment it would run under: `env GIT_EXTERNAL_DIFF=prog "
            "git diff HEAD~1 HEAD` puts the assignment where the guard reads "
            "the verb, and `env -S '...'` hides the whole invocation inside "
            "one word. Run the command as a command of its own."
        )

    if _PYTHON_RE.match(name) and words[1:3] == ["-m", "pytest"]:
        verb, args = "pytest", words[3:]
    elif name == "pytest":
        verb, args = "pytest", words[1:]
    elif name == "git":
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

    # `env` first: an assignment's value is shell text like any other word, so
    # `FOO=$(...) pytest -q` substitutes before pytest starts. Scanning only
    # `words` left that one token to the left of everything the guard reads.
    for token in env + words:
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

    # Every guarded verb, not just pytest: `GIT_EXTERNAL_DIFF=prog git diff`
    # runs `prog` on each changed path, which is further than `--no-index` or
    # `--output` reach.
    for assignment in env:
        matched = _ASSIGNMENT_RE.match(assignment)
        name = matched.group(1) if matched else assignment.partition("=")[0]
        if name not in SAFE_ENV:
            raise Denied(
                f"`{name}=...` in front of `{verb}` changes what it loads or "
                "runs; the guard's safe list "
                f"(.claude/hooks/guard-bash.py) is {', '.join(sorted(SAFE_ENV))}"
            )

    if redirections:
        raise Denied(
            f"`{verb}` with a shell redirection (`{redirections[0]}`) reads or "
            "writes a file of the shell's choosing; the allow list never meant "
            "to grant that. bash accepts one in front of the command name as "
            "readily as after it, so `>out git diff HEAD` is the same command "
            "as `git diff HEAD >out`; a file-descriptor duplication (`2>&1`) "
            "names no path and is left alone"
        )

    if verb == "pytest":
        _check_pytest(args, cwd)
    else:
        _check_git(verb, args, cwd)


def _exports_a_name(words: list[str]) -> bool:
    """Whether this simple command is an export-family builtin naming a variable.

    `export` and `declare -p` on their own name nothing and are not this.

    A name with no `=` counts. `export GIT_EXTERNAL_DIFF` exports the name and
    a later command assigns to it, so
    `export GIT_EXTERNAL_DIFF; GIT_EXTERNAL_DIFF=prog; git diff HEAD` puts the
    program in git's environment exactly as the one-word spelling does, and
    asking only whether the builtin's own word carried an `=` answered no for
    it. Which name is dangerous is deliberately not modelled here: the reach
    is the same whatever it is called, and `SAFE_ENV` is where the names that
    change nothing are listed.
    """

    if not words or words[0] not in _EXPORT_BUILTINS:
        return False
    return any(not word.startswith("-") for word in words[1:])


def _turns_on_allexport(words: list[str]) -> bool:
    """Whether this simple command is the `set` that exports later assignments.

    `set -a` and `set -o allexport` put every variable assigned after them in
    the environment of every command bash then runs, so a plain
    `GIT_EXTERNAL_DIFF=prog` standing as its own command reaches the next git
    invocation without naming an export-family builtin at all. Only the arming
    spellings are read; `set +a` turns it back off and is not matched, which
    can leave the flag set for longer than bash would -- the over-refusing
    direction, and the safe one.
    """

    if not words or words[0] != "set":
        return False
    return any(
        word == "allexport"
        or (word.startswith("-") and not word.startswith("--") and "a" in word[1:])
        for word in words[1:]
    )


def _is_bare_assignment(words: list[str]) -> bool:
    """Whether this simple command is assignments and nothing else.

    On its own that sets a shell variable, which no child process sees, so it
    is not refused; under `set -a` it sets an environment one, and
    `_turns_on_allexport` is what tells the two apart.
    """

    return bool(words) and all(_ASSIGNMENT_RE.match(word) for word in words)


def decide(command: str, cwd: str | os.PathLike[str] | None = None) -> str | None:
    """Return the reason the command is denied, or None to leave it alone."""
    cwd_path = Path(cwd) if cwd else _PROJECT_DIR
    if _SUBSTITUTION_RE.search(command) and _GUARDED_WORD_RE.search(command):
        return (
            "a command substitution next to a guarded command: the guard "
            "cannot see what the shell would run, so it cannot check the "
            "command that would actually run"
        )
    raw_wrapper = _backslash_wrapper(command)
    if raw_wrapper is not None:
        return (
            f"`{raw_wrapper}` is a wrapper spelled with a backslash. Claude "
            "Code's permission matcher cuts a word at its last `/` or `\\` and "
            "steps over the wrapper it finds, so the allow rule matched only "
            "the words after it; bash reads the backslash differently "
            "(`/usr/bin\\timeout` is the file `/usr/bintimeout`), so what runs "
            "is a file the guard never saw, and a redirection on the line is "
            "applied even when that file does not exist. Run the command "
            "without the wrapper"
        )
    try:
        tokens = _tokenise(command)
    except ValueError as exc:
        if _GUARDED_WORD_RE.search(command):
            return f"the guard could not parse this command ({exc}); rephrase it"
        return None
    try:
        # In order: an export reaches the commands bash runs *after* it, so
        # `git diff HEAD; export GIT_EXTERNAL_DIFF=prog` is left alone and
        # `export GIT_EXTERNAL_DIFF=prog; git diff HEAD` is not.
        exported = False
        allexport = False
        for words in _simple_commands(tokens):
            if exported and _GUARDED_WORD_RE.search(" ".join(words)):
                raise Denied(
                    "an export-family name exported earlier in this string "
                    "(`export NAME=value`, `declare -x`, `typeset -x`, "
                    "`readonly`, a bare `export NAME` a later command assigns "
                    "to, or any assignment after `set -a`) "
                    "is in this command's environment, which is the "
                    "reach of a leading `NAME=value` written after the verb "
                    "instead of before it: `export GIT_EXTERNAL_DIFF=prog; "
                    "git diff HEAD~1 HEAD` runs prog once per changed path "
                    "while the git command carries no assignment at all. Run "
                    "it without the export"
                )
            # Armed after this command has been judged, so the rule stays
            # ordered: an export reaches what bash runs after it, never what
            # ran before it.
            if _exports_a_name(words) or (allexport and _is_bare_assignment(words)):
                exported = True
            if _turns_on_allexport(words):
                allexport = True
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
        if not _GUARDED_WORD_RE.search(raw):
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
