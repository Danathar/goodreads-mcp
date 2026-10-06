"""The step reader in `tests/_workflow_steps.py` can pick one job out of many.

`release.yml` is going to grow a second job to publish to PyPI (#153), and the
reader used to insist on exactly one `steps:` list per file. These tests pin
the `job=` selection on a small two-job file, so that change only has to add
the job, not teach the reader about it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _workflow_steps

_TWO_JOBS = """\
name: two jobs

on: push

jobs:
  first:
    runs-on: ubuntu-latest
    outputs:
      done: ${{ steps.a.outputs.done }}
    steps:
      - name: A
        id: a
        run: echo "done=true" >> "$GITHUB_OUTPUT"

      - name: B
        run: |
          echo b

  second:
    needs: first
    runs-on: ubuntu-latest
    steps:
      - name: C
        uses: actions/checkout@v4
"""


@pytest.fixture()
def two_jobs(tmp_path: Path) -> Path:
    path = tmp_path / "two.yml"
    path.write_text(_TWO_JOBS, encoding="utf-8")
    return path


def test_a_multi_job_file_needs_a_job_name(two_jobs: Path):
    with pytest.raises(AssertionError, match="exactly one steps: list"):
        _workflow_steps.Workflow(two_jobs)


def test_each_job_reads_only_its_own_steps(two_jobs: Path, tmp_path: Path):
    first = _workflow_steps.Workflow(two_jobs, job="first")
    second = _workflow_steps.Workflow(two_jobs, job="second")

    assert [step.name for step in first.steps] == ["A", "B"]
    assert first.step("B").run == "echo b\n"
    assert [step.name for step in second.steps] == ["C"]
    assert second.step("C").uses == "actions/checkout@v4"

    # Every job id GitHub accepts is a job here, whatever its spelling. A
    # pattern that skipped one folded its lines into the job above, so a
    # timeout or permissions check never saw it.
    odd_ids = tmp_path / "odd.yml"
    odd_ids.write_text(
        "on: push\n"
        "jobs: # four-space indent\n"
        "    build_2:\n        steps:\n            - name: D\n              run: echo d\n"
        "    \"Quoted\":\n        timeout-minutes: 5\n        steps:\n            - name: E\n              run: echo e\n"
        "    lint: # trailing comment\n        steps:\n            - name: F\n              run: echo f\n",
        encoding="utf-8",
    )
    blocks, key_indent = _workflow_steps.jobs(odd_ids.read_text(encoding="utf-8"))
    assert list(blocks) == ["build_2", "Quoted", "lint"]
    assert key_indent == 8
    assert "        timeout-minutes: 5" in blocks["Quoted"]
    assert "        timeout-minutes: 5" not in blocks["build_2"]
    for job, step in (("build_2", "D"), ("Quoted", "E"), ("lint", "F")):
        assert [s.name for s in _workflow_steps.Workflow(odd_ids, job=job).steps] == [step]


def test_an_unknown_job_is_refused(two_jobs: Path):
    with pytest.raises(AssertionError, match="no job named 'third'"):
        _workflow_steps.Workflow(two_jobs, job="third")


def test_a_single_job_file_reads_the_same_with_or_without_the_name(two_jobs: Path, tmp_path: Path):
    single = tmp_path / "single.yml"
    single.write_text(_TWO_JOBS.split("\n  second:")[0] + "\n", encoding="utf-8")

    unnamed = [step.name for step in _workflow_steps.Workflow(single).steps]
    named = [step.name for step in _workflow_steps.Workflow(single, job="first").steps]
    assert unnamed == named == ["A", "B"]


def test_the_workflow_list_takes_both_suffixes_and_only_top_level_files(tmp_path: Path):
    # GitHub runs `.yml` and `.yaml` files directly under .github/workflows/,
    # and nothing else; every "each workflow" test takes its list from here.
    for name in ("b.yaml", "a.yml", "notes.md"):
        (tmp_path / name).write_text("on: push\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "c.yml").write_text("on: push\n", encoding="utf-8")

    assert [p.name for p in _workflow_steps.workflow_files(tmp_path)] == ["a.yml", "b.yaml"]
