"""Every workflow job has to declare its own `timeout-minutes` (#111).

A job that does not set one inherits GitHub's default ceiling of 360 minutes.
That is not a timeout in any useful sense: a wedged step — a stuck `npx`
install, a live request that never returns, a retry loop whose `sleep` was
mistuned — holds a runner for six hours before anyone is told the job failed.
All five jobs in this repo were in that state until #111.

`nightly-compliance.yml` is the reason this is a test and not just a one-time
edit. Its `live` job retries the live suite with a delay read at runtime from
`.github/auto-qa-tuning.json`, so the worst case is set by a file that is meant
to be tuned. The job-level cap is the only bound that does not move when that
file does.

The assertions are deliberately loose about the *number* and strict about its
presence. Retuning a cap is ordinary maintenance and should not need a test
edit; dropping one, or writing a cap so high it is the default wearing a hat,
is the regression. `_CEILING` is what separates the two.

No PyYAML: the test extra is `pytest` + `pytest-cov` and CI installs nothing
else, so the job blocks are sliced out by indentation the way
`tests/_workflow_steps.py` slices out steps.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"

# Above this, a declared cap stops being a bound worth having. GitHub's own
# default is 360; the slowest job here is the live suite, which is minutes.
_CEILING = 60

# `  <job-id>:` — a key indented exactly two spaces under `jobs:`.
_JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$")

# `    timeout-minutes: <n>` at the job's own key level, not a step's.
_TIMEOUT = re.compile(r"^    timeout-minutes:\s*(\d+)\s*(?:#.*)?$")


def _jobs(path: Path) -> dict[str, list[str]]:
    """Each job in a workflow file, as the lines of its block.

    The block runs from the job's key to the next key at the same indent, so a
    `timeout-minutes` belonging to one job can never be read as another's.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.rstrip() == "jobs:"]
    assert len(starts) == 1, f"{path.name} no longer has exactly one top-level jobs: key"

    jobs: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[starts[0] + 1 :]:
        if line.strip() and not line.startswith(" "):
            break  # a new top-level key; jobs: is over
        match = _JOB.match(line)
        if match:
            current = match.group(1)
            jobs[current] = []
            continue
        if current is not None:
            jobs[current].append(line)

    assert jobs, f"{path.name}: no jobs found under jobs:"
    return jobs


def _workflow_files() -> list[Path]:
    paths = sorted(p for p in _WORKFLOWS.glob("*.yml"))
    assert paths, f"no workflow files under {_WORKFLOWS}"
    return paths


def _every_job() -> list[tuple[Path, str]]:
    return [(path, job) for path in _workflow_files() for job in _jobs(path)]


_ALL_JOBS = _every_job()


@pytest.mark.parametrize(
    "path,job",
    _ALL_JOBS,
    ids=[f"{path.name}:{job}" for path, job in _ALL_JOBS],
)
def test_the_job_declares_a_timeout(path: Path, job: str):
    """A job with no cap runs to GitHub's 360-minute default before failing."""
    declared = [_TIMEOUT.match(line) for line in _jobs(path)[job]]
    found = [match.group(1) for match in declared if match]
    assert found, (
        f"{path.name}: job {job!r} sets no timeout-minutes, so a hung step holds a "
        "runner for GitHub's default 360 minutes. Add `timeout-minutes: <n>` next "
        "to its `runs-on:`."
    )
    assert len(found) == 1, f"{path.name}: job {job!r} sets timeout-minutes {len(found)} times"

    minutes = int(found[0])
    assert 0 < minutes <= _CEILING, (
        f"{path.name}: job {job!r} caps at {minutes} minutes, outside the 1..{_CEILING} "
        "range a useful bound sits in. Raise _CEILING here with a reason if the job "
        "really does need longer."
    )


def test_the_new_workflow_is_covered_too():
    """A sixth workflow file must arrive with this test already applying to it.

    The parametrisation above is built from whatever is on disk, so a new file
    is covered the moment it lands. This asserts the known jobs are still
    among them, which is what catches a file being renamed out of the glob
    rather than fixed.
    """
    seen = {(path.name, job) for path, job in _ALL_JOBS}
    expected = {
        ("ai-fix.yml", "requested"),
        ("ci.yml", "test"),
        ("labeler.yml", "label"),
        ("nightly-compliance.yml", "live"),
        ("release.yml", "prepare"),
        ("release.yml", "release"),
        ("release.yml", "publish-pypi"),
        ("release.yml", "publish-registry"),
    }
    assert expected <= seen, f"workflow jobs have gone missing: {sorted(expected - seen)}"
