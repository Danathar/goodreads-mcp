"""`ci.yml`'s `test` job is the one check `main` requires, and two of its flags
could be deleted with the suite green (#300).

Other tests read `ci.yml` for one thing each — the Python version
(`tests/test_python_floor.py`), the gate number (`tests/test_coverage_thresholds.py`),
the commands the PR checklist quotes (`tests/test_github_templates.py`) — and
`tests/test_merge_queue_workflow.py` holds `merge-queue.yml`'s steps equal to
these. That last one is a mirror, not a check: an edit made to both files
passes it. Edited in both, these survived the full suite:

- **`--resolution lowest-direct`** in the dependency-floor step. Without it uv
  resolves every dependency to its newest release, so the step named "Run tests
  against the declared dependency floors" runs the same versions the step
  before it ran, and stays green. `docs/quality.md` lists the floor run as a
  blocking gate and quotes this exact flag.
- **`--cov=goodreads_mcp`** in the coverage-gate step. `--cov-fail-under` on
  its own does nothing: pytest-cov never starts measuring, so there is no total
  to compare, and the run passes whatever the coverage is. The 55% gate that
  seven prose files quote would be gone.

So both steps are **run**, with the reader and runner in
`tests/_workflow_steps.py`: the floor step against stubs that record what uv
and the floor interpreter were asked to do, and the gate step for real, with
pytest collecting one passing test that touches nothing in the package, which
must fail on coverage alone.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import sys
import tomllib

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _workflow_steps.Workflow(_ROOT / ".github" / "workflows" / "ci.yml", job="test")
_GATE_STEP = "Run tests with coverage gate"
_FLOOR_STEP = "Run tests against the declared dependency floors"
_PACKAGES = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"][
    "build"
]["targets"]["wheel"]["packages"]


# --------------------------------------------------------------------------
# The dependency-floor step
# --------------------------------------------------------------------------


@pytest.fixture
def floor_run(tmp_path: Path):
    """Run the floor step with `python`, `uv` and the floor venv's interpreter stubbed."""
    log = tmp_path / "argv.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runner_temp = tmp_path / "runner-temp"
    (runner_temp / "floor" / "bin").mkdir(parents=True)
    for name in ("python", "uv"):
        _workflow_steps.write_stub(bin_dir, name, _workflow_steps.recorder(log))
    floor_python = _workflow_steps.write_stub(
        runner_temp / "floor" / "bin", "python", _workflow_steps.recorder(log)
    )
    work = tmp_path / "work"
    work.mkdir()
    result = _workflow_steps.run(
        _WORKFLOW.body(_FLOOR_STEP),
        work,
        path_dirs=[bin_dir],
        env={"RUNNER_TEMP": str(runner_temp)},
    )
    assert result.returncode == 0, result.stderr
    return {
        "calls": _workflow_steps.argv(log),
        "python": str(bin_dir / "python"),
        "uv": str(bin_dir / "uv"),
        "floor": str(floor_python),
        "venv": str(runner_temp / "floor"),
    }


def _calls_to(run: dict, program: str, *head: str) -> list[list[str]]:
    return [call[1:] for call in run["calls"] if call[0] == run[program] and call[1:1 + len(head)] == list(head)]


def test_the_floor_install_resolves_direct_dependencies_to_their_lowest_versions(floor_run):
    """Without `lowest-direct` this step tests the newest versions, which the step before it already did."""
    installs = _calls_to(floor_run, "uv", "pip", "install")
    assert len(installs) == 1, floor_run["calls"]
    args = installs[0]
    assert "--resolution" in args, f"the floor install resolves to the newest versions: {args}"
    assert args[args.index("--resolution") + 1] == "lowest-direct", args


def test_the_floor_install_goes_into_the_floor_venv_with_the_test_extra(floor_run):
    (args,) = _calls_to(floor_run, "uv", "pip", "install")
    assert args[args.index("--python") + 1] == floor_run["floor"], args
    assert args[args.index("-e") + 1] == ".[test]", args


def test_the_floor_venv_is_built_from_the_interpreter_setup_python_installed(floor_run):
    (args,) = _calls_to(floor_run, "uv", "venv")
    assert args[args.index("--python") + 1] == floor_run["python"], args
    assert args[-1] == floor_run["venv"], args


def test_the_floor_suite_runs_under_the_floor_interpreter(floor_run):
    """The runner's own `python` has the newest versions installed; the floor venv has the lowest."""
    runs = _calls_to(floor_run, "floor", "-m", "pytest")
    assert len(runs) == 1, floor_run["calls"]
    assert not _calls_to(floor_run, "python", "-m", "pytest"), floor_run["calls"]


def test_the_floor_install_happens_before_the_floor_suite_runs(floor_run):
    order = [
        "install" if call[0] == floor_run["uv"] and call[1:3] == ["pip", "install"] else
        "pytest" if call[0] == floor_run["floor"] and call[1:3] == ["-m", "pytest"] else None
        for call in floor_run["calls"]
    ]
    order = [entry for entry in order if entry]
    assert order == ["install", "pytest"], floor_run["calls"]


def test_the_quality_doc_quotes_the_flags_the_floor_step_passes(floor_run):
    """`docs/quality.md` names the floor gate by its command; that command has to be the one CI runs."""
    text = (_ROOT / "docs" / "quality.md").read_text(encoding="utf-8")
    quoted = re.findall(r"\(`(uv pip install [^`]+)`\)", text)
    assert len(quoted) == 1, f"docs/quality.md quotes {len(quoted)} uv install commands"
    (args,) = _calls_to(floor_run, "uv", "pip", "install")
    words = shlex.split(quoted[0])[3:]
    for i in range(0, len(words), 2):
        flag, value = words[i], words[i + 1]
        assert flag in args and args[args.index(flag) + 1] == value, (
            f"docs/quality.md quotes `{flag} {value}`; the floor step passes {args}"
        )


# --------------------------------------------------------------------------
# The coverage-gate step
# --------------------------------------------------------------------------


def _gate_flags() -> list[str]:
    words = shlex.split(_WORKFLOW.body(_GATE_STEP))
    assert words[0] == "pytest", words
    return words[1:]


def test_the_gate_measures_the_package_the_wheel_ships():
    """`--cov` names what the 55% is a share of; anything else makes the number mean something else."""
    measured = [flag.split("=", 1)[1] for flag in _gate_flags() if flag.startswith("--cov=")]
    assert measured == list(_PACKAGES), f"the gate measures {measured}; the wheel ships {_PACKAGES}"


def test_the_gate_step_fails_a_run_that_covers_too_little(tmp_path: Path):
    """Run the step as written; one passing test that touches nothing must fail on coverage alone."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _workflow_steps.write_stub(bin_dir, "pytest", f'exec "{sys.executable}" -m pytest -p no:cacheprovider "$@"\n')
    work = tmp_path / "work"
    work.mkdir()
    (work / "test_nothing.py").write_text("def test_nothing():\n    pass\n", encoding="utf-8")
    # The suite may itself be running under coverage; keep that out of the child.
    env = {key: "" for key in os.environ if key.startswith(("COV_CORE_", "COVERAGE_"))}
    result = _workflow_steps.run(_WORKFLOW.body(_GATE_STEP), work, path_dirs=[bin_dir], env=env)
    assert "1 passed" in result.stdout, result.stdout + result.stderr
    assert result.returncode != 0, (
        "the coverage-gate step passed a run that covered none of the package; "
        "--cov-fail-under does nothing unless --cov starts the measurement\n" + result.stdout
    )
    assert re.search(r"Required test coverage of \d+(\.\d+)?% not reached", result.stdout), result.stdout
