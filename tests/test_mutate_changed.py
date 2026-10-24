"""Pin how `scripts/mutate_changed.py` builds mutants and reads its list (#408).

The script is the `mutation` job in `ci.yml`: it makes small deliberate
breakages (mutants) on the lines a pull request changes and fails on one no
test notices, unless `tests/mutation-equivalents.txt` lists it. A script that
quietly builds no mutants, or builds them on the wrong line, passes every
change, so its pieces are checked here on small sources held in memory.

The verdict is checked too. The CI job only ever shows the script passing a
change, and a script that stopped counting survivors would pass every change
the same way, so `run()` is driven here over a throwaway git repository with
a one-line change and a test that does or does not notice each mutant.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
import signal
import subprocess

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "mutate_changed.py"
_HOOK = ".claude/hooks/guard-bash.py"


def _load():
    spec = importlib.util.spec_from_file_location("mutate_changed", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mc = _load()


_SOURCE = """\
def f(a, b, items):
    if a < b and not items:
        return a + 1
    while a:
        a = a - 1
        break
    for x in items:
        continue
    flag = True
    return b
"""

# Every mutant of `_SOURCE`, as (line, mutated line stripped).
_EXPECTED = {
    (2, "if a <= b and not items:"),
    (2, "if a < b or not items:"),
    (2, "if not items:"),
    (2, "if a < b:"),
    (2, "if a < b and items:"),
    (2, "if True:"),
    (2, "if False:"),
    (3, "return a + 2"),
    (3, "return a + 0"),
    (3, "return None"),
    (4, "while True:"),
    (4, "while False:"),
    (5, "a = a - 2"),
    (5, "a = a - 0"),
    (6, "pass"),
    (8, "pass"),
    (9, "flag = False"),
    (10, "return None"),
}


# --------------------------------------------------------------------------
# Changed lines
# --------------------------------------------------------------------------


def test_added_lines_reads_the_new_side_of_each_hunk():
    diff = "\n".join(
        [
            "diff --git a/x.py b/x.py",
            "--- a/x.py",
            "+++ b/x.py",
            "@@ -1,2 +3,4 @@ def f():",
            "@@ -5 +9 @@",
            "@@ -7,3 +12,0 @@",
        ]
    )
    assert mc.added_lines(diff) == {3, 4, 5, 6, 9}


def test_only_python_files_in_the_package_and_the_hooks_are_targets():
    assert mc.is_target("goodreads_mcp/server.py")
    assert mc.is_target(_HOOK)
    assert not mc.is_target("tests/test_parsers.py")
    assert not mc.is_target("scripts/mutate_changed.py")
    assert not mc.is_target("goodreads_mcp/README.md")


def test_source_lines_cuts_only_where_the_parser_counts_a_line():
    # A form feed and U+2028 inside string literals: `str.splitlines` cuts at both.
    source = 'x = "a\x0cb"\ny = "\u2028"\nz = 1'
    assert mc.source_lines(source) == ['x = "a\x0cb"\n', 'y = "\u2028"\n', "z = 1"]
    assert len(source.splitlines()) == 5


# --------------------------------------------------------------------------
# Building mutants
# --------------------------------------------------------------------------


def test_every_kind_of_mutant_is_built_on_the_changed_lines():
    found = mc.mutants("x.py", _SOURCE, range(1, 11))
    assert {(m.line, m.now) for m in found} == _EXPECTED


def test_no_mutant_is_built_off_the_changed_lines():
    found = mc.mutants("x.py", _SOURCE, {3})
    assert {(m.line, m.now) for m in found} == {e for e in _EXPECTED if e[0] == 3}
    assert mc.mutants("x.py", _SOURCE, set()) == []


def test_each_mutant_changes_exactly_its_own_line_and_still_parses():
    before = _SOURCE.splitlines()
    for mutant in mc.mutants("x.py", _SOURCE, range(1, 11)):
        after = mc.apply(_SOURCE, mutant).splitlines()
        ast.parse("\n".join(after))
        changed = [i + 1 for i, (old, new) in enumerate(zip(before, after)) if old != new]
        assert changed == [mutant.line], mutant
        assert after[mutant.line - 1].strip() == mutant.now
        assert before[mutant.line - 1].strip() == mutant.was


def test_apply_refuses_a_line_that_no_longer_reads_as_it_did():
    (mutant,) = [m for m in mc.mutants("x.py", _SOURCE, {9})]
    with pytest.raises(ValueError):
        mc.apply(_SOURCE.replace("flag = True", "flag = 1 == 1"), mutant)


def test_a_node_spread_over_two_lines_is_left_alone():
    source = "ok = (a and\n      b)\n"
    assert mc.mutants("x.py", source, {1, 2}) == []


def test_annotations_are_not_mutated():
    source = "def g(x: Literal[1]) -> Literal[True]:\n    return x\n"
    assert {(m.line, m.now) for m in mc.mutants("x.py", source, {1, 2})} == {(2, "return None")}


def test_the_rest_of_the_line_is_kept_as_written():
    source = "y = {'k': 1}  # one\n"
    nows = {m.now for m in mc.mutants("x.py", source, {1})}
    assert nows == {"y = {'k': 2}  # one", "y = {'k': 0}  # one"}


def test_zero_moved_down_is_minus_one():
    nows = {m.now for m in mc.mutants("x.py", "z = s[0]\n", {1})}
    assert nows == {"z = s[1]", "z = s[-1]"}


def test_the_guard_mutants_found_by_hand_are_built():
    """The three fail-open survivors #408 reported, on the guard as it is now."""
    lines = (_ROOT / _HOOK).read_text(encoding="utf-8").splitlines()
    nul = [i + 1 for i, line in enumerate(lines) if '.split("\\0", 1)[0]' in line]
    option = [i + 1 for i, line in enumerate(lines) if line.strip() == 'if word.startswith("--") and "=" in word:']
    assert len(nul) == 1 and len(option) == 1, (nul, option)
    source = (_ROOT / _HOOK).read_text(encoding="utf-8")
    found = mc.mutants(_HOOK, source, {nul[0], option[0]})
    nows = {(m.line, m.now) for m in found}
    assert any(line == nul[0] and m.endswith('.split("\\0", 1)[-1]') for line, m in nows), nows
    assert any(line == nul[0] and m.endswith('.split("\\0", 0)[0]') for line, m in nows), nows
    assert (option[0], "if False:") in nows


# --------------------------------------------------------------------------
# The equivalents list
# --------------------------------------------------------------------------


_ENTRY = """\
file: x.py
line: 3
was: return a + 1
now: return a + 2
why: the caller only reads the sign
"""


def test_an_entry_parses_to_its_key():
    text = "# a comment\n\n" + _ENTRY + "\n" + _ENTRY.replace("line: 3", "line: 4")
    assert mc.parse_equivalents(text) == {
        ("x.py", 3, "return a + 1", "return a + 2"),
        ("x.py", 4, "return a + 1", "return a + 2"),
    }


def test_a_printed_survivor_parses_back_to_the_same_key():
    (mutant,) = [m for m in mc.mutants("x.py", _SOURCE, {9})]
    assert mc.parse_equivalents(mutant.entry()) == {mutant.key}


@pytest.mark.parametrize(
    "broken",
    [
        _ENTRY.replace("why: the caller only reads the sign\n", ""),
        _ENTRY.replace("why: the caller only reads the sign", "why:"),
        _ENTRY.replace("line: 3", "line: three"),
        _ENTRY.replace("file:", "path:"),
        _ENTRY + "now: return a\n",
        "return a + 1\n",
    ],
    ids=["no why", "empty why", "line not a number", "unknown key", "key twice", "not key: value"],
)
def test_a_malformed_entry_is_refused(broken: str):
    with pytest.raises(ValueError):
        mc.parse_equivalents(broken)


def test_the_checked_in_list_parses_and_names_only_targets():
    for file, line, was, now in mc.parse_equivalents(mc.EQUIVALENTS.read_text(encoding="utf-8")):
        assert mc.is_target(file), file
        assert line > 0 and was and now and was != now


def test_the_list_documents_the_keys_the_parser_reads():
    header = mc.EQUIVALENTS.read_text(encoding="utf-8")
    for key in ("file", "line", "was", "now", "why"):
        assert re.search(rf"^#\s+{key}:", header, re.M), key


# --------------------------------------------------------------------------
# Which tests run against a mutant
# --------------------------------------------------------------------------


def test_the_hook_runs_against_every_test_file_that_loads_it_or_its_registration(tmp_path: Path):
    (tmp_path / "test_a.py").write_text('GUARD = "hooks/guard-bash.py"\n', encoding="utf-8")
    (tmp_path / "test_b.py").write_text('S = ".claude/settings.json"\n', encoding="utf-8")
    (tmp_path / "test_c.py").write_text("import goodreads_mcp\n", encoding="utf-8")
    (tmp_path / "_helper.py").write_text('GUARD = "guard-bash.py"\n', encoding="utf-8")
    (tmp_path / "test_mutate_changed.py").write_text('"guard-bash.py goodreads_mcp"\n', encoding="utf-8")

    def names(path: str) -> list[str]:
        return [p.name for p in mc.test_files_for(path, tmp_path)]

    assert names(_HOOK) == ["test_a.py", "test_b.py"]
    assert names("goodreads_mcp/server.py") == ["test_c.py"]


def test_the_guard_runs_against_the_six_files_the_hand_run_used():
    """#408 ran each guard mutant against these; the script must not run fewer."""
    selected = {p.name for p in mc.test_files_for(_HOOK)}
    assert {
        "test_agent_permissions.py",
        "test_gate_refusal_corpus.py",
        "test_security_ai_doc.py",
        "test_review_docs.py",
        "test_agent_audit_workflow.py",
        "test_labeler_config.py",
    } <= selected, selected


# --------------------------------------------------------------------------
# The CI job
# --------------------------------------------------------------------------


def test_the_ci_job_runs_the_script_from_the_merge_base_with_full_history():
    job = _workflow_steps.Workflow(_ROOT / ".github" / "workflows" / "ci.yml", job="mutation")
    assert job.steps[0].uses.startswith("actions/checkout@")
    assert job.steps[0].with_.get("fetch-depth") == "0", "a shallow clone has no merge base"
    run = job.step("Run mutants on changed lines")
    assert run.run.strip() == 'python scripts/mutate_changed.py --base "origin/$BASE_REF"'
    assert run.env.get("BASE_REF") == "${{ github.base_ref }}"


# --------------------------------------------------------------------------
# The verdict on a change
# --------------------------------------------------------------------------

_BEFORE = "def big(n):\n    return False\n"
_AFTER = "def big(n):\n    return n > 1\n"
_CHANGED = "goodreads_mcp/m.py"

# The test file loads the module by path, so the installed package is never
# the one it reads; naming the package is what selects it for a mutant.
_TEST_FILE = """\
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[1] / "goodreads_mcp" / "m.py"
_SPEC = importlib.util.spec_from_file_location("m", _PATH)
m = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m)


def test_m():
{body}
"""

# Kills every mutant of `return n > 1`.
_STRONG = "    assert m.big(2) is True\n    assert m.big(1) is False\n"
# Kills only `return None`.
_WEAK = "    assert m.big(5)\n"
_WEAK_SURVIVORS = {"return n >= 1", "return n > 2", "return n > 0"}


def _git_in(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A repository on branch `pr`, one commit past `main`, that the script reads as its own."""
    # `run()` installs its own SIGINT/SIGTERM/SIGHUP handlers; give pytest its own back.
    names = [name for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name)]
    handlers = {name: signal.getsignal(getattr(signal, name)) for name in names}

    def make(after: str = _AFTER, test_body: str | None = _STRONG, equivalents: str = "", before: str = _BEFORE):
        (tmp_path / "goodreads_mcp").mkdir(exist_ok=True)
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / _CHANGED).write_text(before, encoding="utf-8")
        if test_body is not None:
            (tmp_path / "tests" / "test_m.py").write_text(_TEST_FILE.format(body=test_body), encoding="utf-8")
        (tmp_path / "tests" / "mutation-equivalents.txt").write_text(equivalents, encoding="utf-8")
        _git_in(tmp_path, "init", "-q", "-b", "main")
        _git_in(tmp_path, "add", "-A")
        _git_in(tmp_path, "commit", "-q", "-m", "base")
        _git_in(tmp_path, "checkout", "-q", "-b", "pr")
        (tmp_path / _CHANGED).write_text(after, encoding="utf-8")
        _git_in(tmp_path, "commit", "-q", "--allow-empty", "-am", "change")
        monkeypatch.setattr(mc, "ROOT", tmp_path)
        monkeypatch.setattr(mc, "EQUIVALENTS", tmp_path / "tests" / "mutation-equivalents.txt")
        return tmp_path

    yield make
    for name, handler in handlers.items():
        signal.signal(getattr(signal, name), handler)


def _verdicts(out: str) -> dict[str, str]:
    """{mutated line: verdict} from the script's per-mutant lines."""
    found = re.findall(r"^\[\d+/\d+\] (\w+)\s+goodreads_mcp/m\.py:\d+ \[[^]]+\] (.+)$", out, re.M)
    return {now: verdict for verdict, now in found}


def test_a_change_whose_tests_notice_every_mutant_passes(change, capfd):
    root = change()
    assert mc.run("main", list_only=False, timeout=30) == 0
    out = capfd.readouterr().out
    assert _verdicts(out) == {
        "return n >= 1": "killed",
        "return n > 2": "killed",
        "return n > 0": "killed",
        "return None": "killed",
    }
    assert "no unlisted survivor" in out
    assert (root / _CHANGED).read_text(encoding="utf-8") == _AFTER


def test_a_change_whose_tests_miss_a_mutant_fails_and_prints_its_entry(change, capfd):
    root = change(test_body=_WEAK)
    assert mc.run("main", list_only=False, timeout=30) == 1
    out = capfd.readouterr().out
    verdicts = _verdicts(out)
    assert {now for now, verdict in verdicts.items() if verdict == "SURVIVED"} == _WEAK_SURVIVORS
    assert verdicts["return None"] == "killed"
    assert "3 mutants survived" in out
    # What it prints is what the list takes, so a reviewer can paste it.
    entries = out.split("its entry with the reason:\n", 1)[1]
    keys = mc.parse_equivalents(entries.replace("TODO: why no test can tell this mutant apart", "x"))
    assert keys == {(_CHANGED, 2, "return n > 1", now) for now in _WEAK_SURVIVORS}
    assert (root / _CHANGED).read_text(encoding="utf-8") == _AFTER


def test_a_survivor_the_list_names_passes_and_one_it_does_not_still_fails(change, capfd):
    listed = sorted(_WEAK_SURVIVORS)
    entry = "file: {}\nline: 2\nwas: return n > 1\nnow: {}\nwhy: x\n"
    change(test_body=_WEAK, equivalents="\n".join(entry.format(_CHANGED, now) for now in listed))
    assert mc.run("main", list_only=False, timeout=30) == 0
    out = capfd.readouterr().out
    assert {now for now, verdict in _verdicts(out).items() if verdict == "listed"} == _WEAK_SURVIVORS
    assert "3 survivors are listed" in out

    # The key carries the line: the same entry one line off excuses nothing.
    mc.EQUIVALENTS.write_text(
        "\n".join(entry.format(_CHANGED, now).replace("line: 2", "line: 3") for now in listed), encoding="utf-8"
    )
    assert mc.run("main", list_only=False, timeout=30) == 1


def test_a_changed_file_no_test_loads_fails(change, capfd):
    root = change(test_body=None)
    assert mc.run("main", list_only=False, timeout=30) == 1
    captured = capfd.readouterr()
    assert "no test file loads it" in captured.err
    assert set(_verdicts(captured.out).values()) == {"SURVIVED"}
    assert (root / _CHANGED).read_text(encoding="utf-8") == _AFTER


def test_tests_that_already_fail_stop_the_run_before_any_mutant(change, capfd):
    root = change(test_body="    assert False\n")
    assert mc.run("main", list_only=False, timeout=30) == 2
    captured = capfd.readouterr()
    assert "fail on the unmodified code" in captured.err
    assert _verdicts(captured.out) == {}
    assert (root / _CHANGED).read_text(encoding="utf-8") == _AFTER


def test_a_list_that_does_not_parse_stops_the_run(change, capfd):
    change(equivalents="file: goodreads_mcp/m.py\n")
    assert mc.run("main", list_only=False, timeout=30) == 2
    assert "has no line, was, now, why" in capfd.readouterr().err


def test_a_change_outside_the_targets_passes_without_running_anything(change, capfd, monkeypatch):
    root = change(after=_BEFORE)
    (root / "tests" / "notes.py").write_text("x = 1\n", encoding="utf-8")
    _git_in(root, "add", "-A")
    _git_in(root, "commit", "-q", "-m", "outside")
    monkeypatch.setattr(mc, "_run_tests", lambda *a, **k: pytest.fail("ran tests"))
    assert mc.run("main", list_only=False, timeout=30) == 0
    assert "no changed .py lines" in capfd.readouterr().out


def test_a_mutant_that_hangs_counts_as_killed(change, capfd):
    before = "def count(n):\n    while n > 0:\n        n -= 2\n    return n\n"
    root = change(before=before, after=before.replace("n -= 2", "n -= 1"), test_body="    assert m.count(3) == 0\n")
    assert mc.run("main", list_only=False, timeout=8) == 0
    out = capfd.readouterr().out
    assert _verdicts(out) == {"n -= 2": "killed", "n -= 0": "killed"}
    assert "n -= 1" in (root / _CHANGED).read_text(encoding="utf-8")


def test_the_file_is_restored_when_the_run_is_stopped_mid_mutant(change, capfd, monkeypatch):
    root = change()
    seen = []

    def stopped(tests, timeout, cache, quiet=True):
        if not quiet:
            return "passed"  # the clean run
        seen.append((root / _CHANGED).read_text(encoding="utf-8"))
        raise mc._Stop("SIGTERM")

    monkeypatch.setattr(mc, "_run_tests", stopped)
    assert mc.run("main", list_only=False, timeout=30) == 1
    assert len(seen) == 1 and seen[0] != _AFTER, "the mutant was on disk when the run stopped"
    assert (root / _CHANGED).read_text(encoding="utf-8") == _AFTER
    assert "stopped by SIGTERM" in capfd.readouterr().err


def test_list_prints_the_mutants_and_runs_no_test(change, capfd, monkeypatch):
    change()
    monkeypatch.setattr(mc, "_run_tests", lambda *a, **k: pytest.fail("ran tests"))
    assert mc.main(["--base", "main", "--list"]) == 0
    out = capfd.readouterr().out
    assert f"{_CHANGED}: 1 changed lines, 4 mutants, 1 test files" in out
    assert "-> return n >= 1" in out
