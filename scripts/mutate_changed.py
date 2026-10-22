#!/usr/bin/env python3
"""Break the lines a change touches, one small edit at a time, and run the tests.

A green suite at 100% line coverage still lets a change merge whose tests run
a branch but never check what it decides. Mutation testing asks the sharper
question: make one small edit to the code (a mutant), run the tests that load
it, and see whether any test fails. A mutant no test notices is a survivor.
#352, #379, #382, #393 and #408 each found survivors by hand, three of them
in the command guard letting a refused command through (#408).

This script does that for the lines a pull request changes, so the cost
follows the size of the change:

1. Every `.py` file under `goodreads_mcp/` and `.claude/hooks/` that differs
   from the merge base with `--base` (default `origin/main`) is read, and the
   lines added or changed since then are collected.
2. On those lines it builds single-line mutants from the AST: a comparison
   swapped, `and`/`or` swapped, one operand of a boolean operator dropped,
   `not` dropped, an integer moved by one, `True`/`False` flipped, an `if` or
   `while` test forced to `True` or `False`, `continue`/`break` replaced by
   `pass`, and `return x` replaced by `return None`. Only the mutated node's
   own text on its line is replaced, and the result must parse to exactly the
   tree the mutation describes, so a mutant never differs in more than the
   one place it says.
3. Each mutant is written over the file in place, the test files that load
   that file are run with `python -B -m pytest -x -q`, and the original bytes
   are written back, always: on success, on failure, on a timeout and on
   SIGINT, SIGTERM or SIGHUP. `-B` and an empty `PYTHONPYCACHEPREFIX` keep a
   mutant written and restored within one second from being read back from a
   stale `__pycache__` entry on the next run. A mutant whose run outlasts
   `--timeout` (by default three times a clean run) counts as killed: it
   made the code hang, which a test noticed.
4. A survivor listed in `tests/mutation-equivalents.txt` (a reviewer judged
   it equivalent or message-only) is reported and passes. Any other survivor
   is printed in that file's format and the script exits 1.

Exit status: 0 no unlisted survivor; 1 at least one; 2 the tests already fail
on the unmodified code, or the equivalents file does not parse.

`--list` prints the mutants and runs nothing. Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import copy
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Iterable, Iterator, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
EQUIVALENTS = ROOT / "tests" / "mutation-equivalents.txt"

# The source trees a mutant may be made in.
TARGETS = ("goodreads_mcp/", ".claude/hooks/")

# Never selected as a test file for a mutant: it reads this script, not the code.
_SELF_TEST = "test_mutate_changed.py"

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

_COMPARE_SWAP = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}

_EQUIVALENT_KEYS = ("file", "line", "was", "now", "why")


class Mutant(NamedTuple):
    """One mutant: where it is, the line before and after, and what it does."""

    file: str
    line: int
    was: str
    now: str
    kind: str

    @property
    def key(self) -> tuple[str, int, str, str]:
        return (self.file, self.line, self.was, self.now)

    def entry(self, why: str = "TODO: why no test can tell this mutant apart") -> str:
        """The mutant as an entry of `tests/mutation-equivalents.txt`."""
        return (
            f"file: {self.file}\nline: {self.line}\nwas: {self.was}\n"
            f"now: {self.now}\nwhy: {why}\n"
        )


# --------------------------------------------------------------------------
# The changed lines
# --------------------------------------------------------------------------


def _git(*args: str) -> str:
    # core.fsmonitor off: a checkout's config must not run a program here.
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def added_lines(diff: str) -> set[int]:
    """The new-side line numbers a `git diff -U0` adds or changes."""
    lines: set[int] = set()
    for row in diff.splitlines():
        match = _HUNK.match(row)
        if match:
            start = int(match[1])
            count = 1 if match[2] is None else int(match[2])
            lines.update(range(start, start + count))
    return lines


def source_lines(source: str) -> list[str]:
    """`source` cut into lines where the parser counts them, endings kept.

    `str.splitlines` also cuts at a form feed or U+2028 inside a string
    literal, which would put every later line number off by one.
    """
    return re.findall(r"[^\n]*\n|[^\n]+$", source)


def is_target(path: str) -> bool:
    return path.endswith(".py") and path.startswith(TARGETS)


def changed_files(base: str) -> dict[str, set[int]]:
    """Each target file that differs from the merge base, with its changed lines."""
    merge_base = _git("merge-base", base, "HEAD").strip()
    names = _git("diff", "--name-only", "--diff-filter=AMR", merge_base, "--", *TARGETS).split("\n")
    changed: dict[str, set[int]] = {}
    for name in sorted(n for n in names if is_target(n)):
        lines = added_lines(_git("diff", "-U0", "--no-color", "--no-ext-diff", merge_base, "--", name))
        if lines:
            changed[name] = lines
    return changed


# --------------------------------------------------------------------------
# Building mutants
# --------------------------------------------------------------------------


def _alternatives(node: ast.AST, parent: ast.AST | None) -> Iterator[tuple[str, ast.AST]]:
    """(kind, replacement node) for each mutant of one node."""
    if isinstance(node, ast.Compare):
        for i, op in enumerate(node.ops):
            swap = _COMPARE_SWAP.get(type(op))
            if swap:
                new = copy.deepcopy(node)
                new.ops[i] = swap()
                yield "comparison swapped", new
    elif isinstance(node, ast.BoolOp):
        new = copy.deepcopy(node)
        new.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        yield "and/or swapped", new
        for i in range(len(node.values)):
            rest = [copy.deepcopy(v) for j, v in enumerate(node.values) if j != i]
            kept = rest[0] if len(rest) == 1 else ast.BoolOp(op=copy.deepcopy(node.op), values=rest)
            yield "operand dropped", kept
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield "not dropped", copy.deepcopy(node.operand)
    elif isinstance(node, ast.Constant) and type(node.value) is bool:
        yield "True/False flipped", ast.Constant(value=not node.value)
    elif isinstance(node, ast.Constant) and type(node.value) is int:
        yield "integer moved by one", _int(node.value + 1)
        yield "integer moved by one", _int(node.value - 1)
    elif isinstance(node, (ast.Continue, ast.Break)):
        yield f"{type(node).__name__.lower()} dropped", ast.Pass()
    elif isinstance(node, ast.Return) and node.value is not None:
        if not (isinstance(node.value, ast.Constant) and node.value.value is None):
            yield "return None", ast.Return(value=ast.Constant(value=None))
    # A forced test replaces the `if`/`while` test expression, not the statement.
    if parent is not None and isinstance(parent, (ast.If, ast.While, ast.IfExp)) and parent.test is node:
        for value in (True, False):
            if not (isinstance(node, ast.Constant) and node.value is value):
                yield f"{type(parent).__name__} test forced {value}", ast.Constant(value=value)


def _int(value: int) -> ast.expr:
    """An integer literal as the parser builds it: `-1` is `-` applied to `1`."""
    if value < 0:
        return ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=-value))
    return ast.Constant(value=value)


def _with_parents(tree: ast.AST) -> Iterator[tuple[ast.AST, ast.AST | None]]:
    stack: list[tuple[ast.AST, ast.AST | None]] = [(tree, None)]
    while stack:
        node, parent = stack.pop()
        yield node, parent
        stack.extend((child, node) for child in reversed(list(ast.iter_child_nodes(node))))


class _Replace(ast.NodeTransformer):
    """Swap the one node at a given position for its replacement."""

    def __init__(self, where: tuple, replacement: ast.AST):
        self.where = where
        self.replacement = replacement

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if _position(node) == self.where:
            return self.replacement
        return super().generic_visit(node)


def _position(node: ast.AST) -> tuple:
    return (
        type(node).__name__,
        getattr(node, "lineno", None),
        getattr(node, "col_offset", None),
        getattr(node, "end_lineno", None),
        getattr(node, "end_col_offset", None),
    )


def _annotation_ids(tree: ast.AST) -> set[int]:
    """Every node inside an annotation, which runs nothing under `from __future__ import annotations`."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        annotations = []
        if isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            annotations.append(node.returns)
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        for annotation in annotations:
            ids.update(id(n) for n in ast.walk(annotation))
    return ids


def mutants(path: str, source: str, lines: Iterable[int]) -> list[Mutant]:
    """Every single-line mutant of `source` on the given 1-based lines."""
    wanted = set(lines)
    tree = ast.parse(source)
    text_lines = source_lines(source)
    skip = _annotation_ids(tree)
    found: dict[tuple, Mutant] = {}
    for node, parent in _with_parents(tree):
        lineno = getattr(node, "lineno", None)
        if lineno not in wanted or getattr(node, "end_lineno", None) != lineno:
            continue
        if id(node) in skip:
            continue
        for kind, replacement in _alternatives(node, parent):
            mutant = _render(path, source, text_lines, tree, node, replacement, kind)
            if mutant is not None and mutant.key not in found:
                found[mutant.key] = mutant
    return sorted(found.values(), key=lambda m: (m.line, m.now))


def _render(
    path: str,
    source: str,
    text_lines: list[str],
    tree: ast.AST,
    node: ast.AST,
    replacement: ast.AST,
    kind: str,
) -> Mutant | None:
    """The mutant's one-line text, or None when no faithful one-line edit exists."""
    index = node.lineno - 1
    raw = text_lines[index].encode("utf-8")  # col offsets count UTF-8 bytes
    head, tail = raw[: node.col_offset], raw[node.end_col_offset :]
    expected = ast.dump(_Replace(_position(node), replacement).visit(copy.deepcopy(tree)))
    text = ast.unparse(replacement)
    for candidate in (text, f"({text})"):
        line = (head + candidate.encode("utf-8") + tail).decode("utf-8")
        mutated = "".join(text_lines[:index]) + line + "".join(text_lines[index + 1 :])
        try:
            if ast.dump(ast.parse(mutated)) == expected:
                was = text_lines[index].strip()
                now = line.strip()
                if was == now:
                    return None
                return Mutant(path, node.lineno, was, now, kind)
        except SyntaxError:
            continue
    return None


def apply(source: str, mutant: Mutant) -> str:
    """`source` with the mutant's line in place of the original one."""
    lines = source_lines(source)
    original = lines[mutant.line - 1]
    if original.strip() != mutant.was:
        raise ValueError(f"{mutant.file}:{mutant.line} no longer reads {mutant.was!r}")
    indent = original[: len(original) - len(original.lstrip())]
    ending = original[len(original.rstrip("\r\n")) :]
    lines[mutant.line - 1] = indent + mutant.now + ending
    return "".join(lines)


# --------------------------------------------------------------------------
# The equivalents list
# --------------------------------------------------------------------------


def parse_equivalents(text: str) -> set[tuple[str, int, str, str]]:
    """The (file, line, was, now) keys of `tests/mutation-equivalents.txt`.

    Entries are blocks of `key: value` lines separated by blank lines; `#`
    starts a comment line. Each entry has exactly `file`, `line`, `was`,
    `now` and a non-empty `why`.
    """
    keys: set[tuple[str, int, str, str]] = set()
    block: dict[str, str] = {}
    start = 0

    def close(end: int) -> None:
        if not block:
            return
        where = f"lines {start}-{end}"
        missing = [k for k in _EQUIVALENT_KEYS if not block.get(k)]
        if missing:
            raise ValueError(f"equivalents entry at {where} has no {', '.join(missing)}")
        if not block["line"].isdigit():
            raise ValueError(f"equivalents entry at {where}: line {block['line']!r} is not a number")
        keys.add((block["file"], int(block["line"]), block["was"], block["now"]))
        block.clear()

    rows = text.splitlines()
    for number, row in enumerate(rows, 1):
        if row.startswith("#"):
            continue
        if not row.strip():
            close(number - 1)
            continue
        key, sep, value = row.partition(":")
        if not sep or key not in _EQUIVALENT_KEYS:
            raise ValueError(f"equivalents line {number}: expected one of {_EQUIVALENT_KEYS}, got {row!r}")
        if key in block:
            raise ValueError(f"equivalents line {number}: {key!r} given twice in one entry")
        if not block:
            start = number
        block[key] = value.strip()
    close(len(rows))
    return keys


# --------------------------------------------------------------------------
# Running the tests
# --------------------------------------------------------------------------


def test_files_for(path: str, tests_dir: Path | None = None) -> list[Path]:
    """The test files that load `path`.

    A hook is loaded by file path or run through `.claude/settings.json`, so a
    test file that names either is one of its tests. A package module is
    reached through the package, so every test file that names the package is.
    The live suite under `tests/e2e/` skips offline and is left out.
    """
    tests_dir = tests_dir or ROOT / "tests"
    if path.startswith(".claude/hooks/"):
        needles = (Path(path).name, ".claude/settings.json")
    else:
        needles = ("goodreads_mcp",)
    return [
        test
        for test in sorted(tests_dir.glob("test_*.py"))
        if test.name != _SELF_TEST
        and any(needle in test.read_text(encoding="utf-8") for needle in needles)
    ]


def _run_tests(tests: list[Path], timeout: float, cache: str, quiet: bool = True) -> str:
    """The outcome of one run: "passed", "failed" or "timeout"."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX=cache)
    command = [sys.executable, "-B", "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *map(str, tests)]
    output = subprocess.DEVNULL if quiet else None
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=output, stderr=output, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout"
    return "passed" if result.returncode == 0 else "failed"


class _Stop(BaseException):
    """Raised from a signal handler so the `finally` that restores the file runs."""


def _install_signal_handlers() -> None:
    def stop(signum, _frame):
        raise _Stop(signal.Signals(signum).name)

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)


def run(base: str, list_only: bool, timeout: float | None) -> int:
    changed = changed_files(base)
    if not changed:
        print(f"no changed .py lines under {', '.join(TARGETS)} since the merge base with {base}")
        return 0
    try:
        listed = parse_equivalents(EQUIVALENTS.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"{EQUIVALENTS.relative_to(ROOT)}: {exc}", file=sys.stderr)
        return 2

    plan: list[tuple[str, list[Path], list[Mutant]]] = []
    for path, lines in changed.items():
        source = (ROOT / path).read_text(encoding="utf-8")
        found = mutants(path, source, lines)
        tests = test_files_for(path)
        plan.append((path, tests, found))
        print(f"{path}: {len(lines)} changed lines, {len(found)} mutants, {len(tests)} test files")
        if list_only:
            for m in found:
                print(f"  {m.line}: [{m.kind}] {m.was}\n  {' ' * len(str(m.line))}  -> {m.now}")
    if list_only:
        return 0

    _install_signal_handlers()
    survivors: list[Mutant] = []
    excused: list[Mutant] = []
    with tempfile.TemporaryDirectory(prefix="mutate-pycache-") as cache:
        for path, tests, found in plan:
            if not found:
                continue
            limit = timeout or 600.0
            if not tests:
                print(f"{path}: no test file loads it; every mutant survives", file=sys.stderr)
            else:
                started = time.monotonic()
                if _run_tests(tests, limit, cache, quiet=False) != "passed":
                    print(f"{path}: its tests fail on the unmodified code; fix them first", file=sys.stderr)
                    return 2
                # A mutant that makes a loop run forever is killed by the clock.
                # Three times a clean run, and at least a minute, is that clock.
                if timeout is None:
                    limit = max(60.0, 3 * (time.monotonic() - started))
            target = ROOT / path
            original = target.read_bytes()
            source = original.decode("utf-8")
            try:
                for n, mutant in enumerate(found, 1):
                    target.write_bytes(apply(source, mutant).encode("utf-8"))
                    try:
                        outcome = _run_tests(tests, limit, cache) if tests else "passed"
                    finally:
                        target.write_bytes(original)
                    verdict = "killed" if outcome != "passed" else (
                        "listed" if mutant.key in listed else "SURVIVED"
                    )
                    print(f"[{n}/{len(found)}] {verdict:8} {path}:{mutant.line} [{mutant.kind}] {mutant.now}")
                    if verdict == "SURVIVED":
                        survivors.append(mutant)
                    elif verdict == "listed":
                        excused.append(mutant)
            except _Stop as exc:
                print(f"stopped by {exc}; {path} restored", file=sys.stderr)
                return 1
            finally:
                target.write_bytes(original)

    if excused:
        print(f"\n{len(excused)} survivors are listed in {EQUIVALENTS.relative_to(ROOT)}")
    if survivors:
        print(
            f"\n{len(survivors)} mutants survived that {EQUIVALENTS.relative_to(ROOT)} does not list. "
            "Add a test that fails on each, or, if no test can tell it apart from the "
            "original, add its entry with the reason:\n"
        )
        print("\n".join(m.entry() for m in survivors))
        return 1
    print("no unlisted survivor")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="origin/main", help="the ref whose merge base the change is measured from")
    parser.add_argument("--list", action="store_true", help="print the mutants and run no tests")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="seconds per test run, after which the mutant counts as killed "
        "(default: three times the clean run, at least 60)",
    )
    args = parser.parse_args(argv)
    # A runner's stdout is a pipe; print each verdict as it lands, not at exit.
    sys.stdout.reconfigure(line_buffering=True)
    return run(args.base, args.list, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
