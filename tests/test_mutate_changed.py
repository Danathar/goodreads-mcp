"""Pin how `scripts/mutate_changed.py` builds mutants and reads its list (#408).

The script is the `mutation` job in `ci.yml`: it makes small deliberate
breakages (mutants) on the lines a pull request changes and fails on one no
test notices, unless `tests/mutation-equivalents.txt` lists it. A script that
quietly builds no mutants, or builds them on the wrong line, passes every
change, so its pieces are checked here on small sources held in memory. No
test here runs pytest, git or a mutant; the CI job is what does that.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re

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
