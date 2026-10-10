"""Every copy of the minimum Python version agrees with `requires-python`.

`pyproject.toml`'s `requires-python` is the floor. It is also written in
`manifest.json` (what an MCPB host checks before it launches the bundle), in
every workflow's `setup-python` step, and in the install instructions of
README.md, CONTRIBUTING.md and CLAUDE.md. #253 found the floor promising 3.10
while no CI job had ever run 3.10; #259 raised it to 3.11 and edited each copy
by hand. Nothing joined them, so the next change to one could leave the others
behind with the suite green.

`ci.yml`'s `test` job matters most. Its dependency-floor step builds its venv
from the interpreter `setup-python` installed, so that job is the only place
the lowest supported Python and the lowest dependency versions run together. It
has to be the floor exactly, not just something at or above it.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path

import _workflow_steps

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_REQUIRES = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["requires-python"]

# Standard-library modules newer than some Python 3 release, and the release
# that added them. A module imported unconditionally needs at least that floor.
_STDLIB_ADDED = {"tomllib": (3, 11)}


def _version(text: str) -> tuple[int, int]:
    major, minor = text.split(".")
    return int(major), int(minor)


def _floor() -> tuple[int, int]:
    match = re.fullmatch(r">=\s*(\d+\.\d+)", _REQUIRES)
    assert match, f"requires-python is {_REQUIRES!r}; this test reads only a plain '>=X.Y'"
    return _version(match.group(1))


def _setup_python_steps(path: Path) -> list[tuple[str, str]]:
    """(step name, python-version) for every `actions/setup-python` step in a workflow.

    Read by indentation, like the other workflow tests: the test extra ships no
    YAML parser.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    found = []
    for i, line in enumerate(lines):
        if not re.match(r"\s+(?:- )?uses: actions/setup-python@", line):
            continue
        name = None
        for back in range(i - 1, max(i - 4, -1), -1):
            named = re.match(r"\s+- name: (.+)$", lines[back])
            if named:
                name = named.group(1).strip()
                break
        version = None
        for ahead in lines[i + 1 : i + 6]:
            pinned = re.match(r"\s+python-version: \"?([0-9.]+)\"?\s*$", ahead)
            if pinned:
                version = pinned.group(1)
                break
        assert version, f"{path.name}:{i + 1}: setup-python with no python-version in the next lines"
        found.append((name, version))
    return found


def _tracked(*patterns: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", *patterns], cwd=_ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [_ROOT / p for p in out.splitlines()]


def test_requires_python_is_a_plain_floor():
    assert _floor() >= (3, 0)


def test_the_bundle_manifest_declares_the_same_floor():
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    runtime = manifest["compatibility"]["runtimes"]["python"]
    assert runtime == _REQUIRES.replace(" ", ""), (
        f"manifest.json runtimes.python is {runtime!r}, pyproject.toml requires-python is {_REQUIRES!r}"
    )


def test_the_ci_test_job_runs_the_floor_exactly():
    """The dependency-floor step uses this interpreter, so CI tests the floor only if it is the floor."""
    steps = _setup_python_steps(_WORKFLOWS / "ci.yml")
    assert len(steps) == 2, steps  # the floor job, then the newest-Python job (#363)
    body = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert 'uv venv --python "$(command -v python)"' in body, (
        "ci.yml's dependency-floor venv no longer takes setup-python's interpreter; "
        "the floor is then tested somewhere else, or nowhere"
    )
    assert _version(steps[0][1]) == _floor(), (
        f"ci.yml sets up Python {steps[0][1]}, requires-python is {_REQUIRES!r}: no CI job runs the floor"
    )


def test_ci_also_runs_the_offline_suite_on_a_newer_python():
    """The floor job cannot see a break on the Python a bundle user's uv picks (#363)."""
    steps = _setup_python_steps(_WORKFLOWS / "ci.yml")
    assert _version(steps[1][1]) > _floor(), f"ci.yml's second setup-python is {steps[1][1]}, not above the floor"
    body = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^  test-newest-python:\s*$", body, re.M)


def test_no_workflow_sets_up_a_python_below_the_floor_or_misnames_it():
    seen = 0
    for path in _workflow_steps.workflow_files():
        for name, version in _setup_python_steps(path):
            seen += 1
            assert _version(version) >= _floor(), f"{path.name}: python-version {version} is below {_REQUIRES!r}"
            assert name == f"Set up Python {version}", f"{path.name}: step {name!r} installs {version}"
    assert seen >= 5, f"found only {seen} setup-python steps; the reader is broken"


# Prose that states a Python version a reader is meant to use: a venv command, or
# "Python >= X" / "Python X or newer".
_PROSE = re.compile(
    r"\bpython(?P<venv>3\.\d+)\s+-m\s+venv\b"
    r"|\bPython\s*(?:≥|>=)\s*(?P<ge>3\.\d+)"
    r"|\bPython\s+(?P<newer>3\.\d+)\s+or\s+newer\b"
)


def _prose_claims() -> list[tuple[str, int, str]]:
    claims = []
    for path in _tracked("*.md", "**/*.md"):
        if "fixtures" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _PROSE.finditer(line):
                version = next(v for v in match.groups() if v)
                claims.append((str(path.relative_to(_ROOT)), lineno, version))
    return claims


def test_the_pattern_reads_each_way_the_docs_state_a_version():
    examples = {
        "python3.11 -m venv .venv": "3.11",
        "Requires Python ≥ 3.11.": "3.11",
        "any Python >= 3.12": "3.12",
        "need Python 3.13 or newer": "3.13",
    }
    for text, version in examples.items():
        match = _PROSE.search(text)
        assert match and next(v for v in match.groups() if v) == version, text
    assert not _PROSE.search("pydantic 3.11 or newer")


def test_every_version_the_docs_tell_a_reader_to_use_is_the_floor():
    claims = _prose_claims()
    files = {path for path, _, _ in claims}
    assert {"README.md", "CONTRIBUTING.md", "CLAUDE.md"} <= files, (
        f"expected install instructions in README, CONTRIBUTING and CLAUDE.md; found them in {sorted(files)}"
    )
    floor = "%d.%d" % _floor()
    wrong = [f"{path}:{lineno} says {version}" for path, lineno, version in claims if version != floor]
    assert wrong == [], f"requires-python is {_REQUIRES!r}, but: {wrong}"


def test_the_floor_is_new_enough_for_every_unconditional_stdlib_import():
    """A module-level import outside `try` runs on every supported Python."""
    needed: dict[str, tuple[int, int]] = {}
    for path in _tracked("*.py", "**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in _STDLIB_ADDED:
                    needed[str(path.relative_to(_ROOT))] = _STDLIB_ADDED[top]
    assert needed, "no module imports tomllib at top level; this test reads nothing"
    too_old = {path: v for path, v in needed.items() if v > _floor()}
    assert too_old == {}, f"requires-python is {_REQUIRES!r}, but these need newer: {too_old}"
