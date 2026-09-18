"""`nightly-compliance.yml` is the repo's only drift detector; nothing ran it.

The offline suite is fixtures, so by construction it cannot see a Goodreads
markup, schema, WAF or GraphQL-config change. `README.md` and `docs/quality.md`
both answer that with the same thing: a scheduled run of the live suite. That
run is five `run:` bodies in one workflow file, and until this file no test
opened it — `ci.yml`, `labeler.yml` and `release.yml` are read by tests, and
`ai-fix.yml` and this one were read by nothing. Since the coverage gate measures
`goodreads_mcp` only, every one of those lines could be deleted without moving
the number a point.

So these tests **run the steps**, with the reader and runner in
`tests/_workflow_steps.py` that `test_release_workflow.py` already uses: the
body is sliced out by indentation, `${{ }}` is substituted from a table that
raises on anything unknown, and the result is handed to `bash --noprofile
--norc -e` — the shell GitHub actually uses, which does **not** set `pipefail`,
because no step, job or workflow here sets `shell:`.

Four things were asserted by nothing and are asserted here:

- **The retry loop.** The whole point of the nightly is that a red run means
  "Goodreads changed", so a transient network error must not raise that alarm.
  One `until` loop decides that. It is run green, red-then-green, and red to
  exhaustion, and the knob that bounds it is shown to change the behaviour.
- **`set -o pipefail`.** `pytest | tee live.log` reports `tee`'s status, so
  without that one line a failing live suite is a green step and the nightly
  detects nothing, silently. The body is run with the line removed to show it.
- **The tuning join.** `.github/auto-qa-tuning.json` says of itself that it is
  "Read by .github/workflows/nightly-compliance.yml". `jq -r '.live.retries //
  1'` falls back to the default when the key is renamed rather than failing, so
  a rename there un-tunes the nightly in silence. The paths are joined to the
  committed file, and the two independent copies of each default — the `jq`
  fallback and the shell's `${RETRIES:-1}` — are joined to each other.
- **The drift issue.** It is the whole output of a failed run. It must go to an
  existing open issue when there is one and open a new one otherwise; the
  reverse is a new issue every night until someone notices.

A `run:` step that is not in `_EXECUTED` below fails the last test in the file,
so a new step cannot be added without either running it or saying out loud that
it is not run.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_NIGHTLY = _ROOT / ".github" / "workflows" / "nightly-compliance.yml"
_TUNING = _ROOT / ".github" / "auto-qa-tuning.json"
_README = _ROOT / "README.md"
_E2E = _ROOT / "tests" / "e2e"

_INSTALL_STEP = "Install package and test deps"
_TUNING_STEP = "Read QA tuning thresholds"
_LIVE_STEP = "Run live suite against real Goodreads endpoints"
_SUMMARY_STEP = "Summarize"
_ISSUE_STEP = "Open or update a drift issue on failure"

_EXECUTED = {
    _INSTALL_STEP,
    _TUNING_STEP,
    _LIVE_STEP,
    _SUMMARY_STEP,
    _ISSUE_STEP,
}

_WORKFLOW = _workflow_steps.Workflow(_NIGHTLY)
_STEPS = _WORKFLOW.steps
_step = _WORKFLOW.step
_write_stub = _workflow_steps.write_stub
_recorder = _workflow_steps.recorder
_argv = _workflow_steps.argv
_outputs = _workflow_steps.outputs
_run = _workflow_steps.run

_TITLE = "Nightly live check failing — Goodreads may have changed"

# `jq -r '<path> // <default>'`, as the tuning step writes it.
_JQ_FILTER = re.compile(r"jq -r '([^']+)'")

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="the tuning step shells out to jq")


def _body(name: str, values: dict[str, str] | None = None) -> str:
    return _WORKFLOW.body(name, values)


def _bin(tmp_path: Path) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir(exist_ok=True)
    return directory


# --------------------------------------------------------------------------
# Install package and test deps
# --------------------------------------------------------------------------


def test_the_install_step_installs_the_test_extra_with_the_selected_interpreter(tmp_path: Path):
    """`python -m pip` for the upgrade, then the editable install with `[test]`.

    Without the extra there is no `pytest` for the next step to run, and the
    nightly fails for a reason that has nothing to do with Goodreads.
    """
    log = tmp_path / "argv"
    stubs = _bin(tmp_path)
    _write_stub(stubs, "python", _recorder(log))
    _write_stub(stubs, "pip", _recorder(log))

    result = _run(_body(_INSTALL_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0, result.stderr
    calls = [[Path(record[0]).name, *record[1:]] for record in _argv(log)]
    assert calls == [
        ["python", "-m", "pip", "install", "--upgrade", "pip"],
        ["pip", "install", "-e", ".[test]"],
    ]


def test_a_failed_install_stops_the_nightly(tmp_path: Path):
    """`-e` is what keeps a broken install from being reported as upstream drift."""
    stubs = _bin(tmp_path)
    _write_stub(stubs, "python", "exit 0\n")
    _write_stub(stubs, "pip", "exit 1\n")

    result = _run(_body(_INSTALL_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode != 0


# --------------------------------------------------------------------------
# Read QA tuning thresholds
# --------------------------------------------------------------------------


def _tuning_tree(tmp_path: Path, payload: object) -> Path:
    github = tmp_path / ".github"
    github.mkdir(exist_ok=True)
    (github / "auto-qa-tuning.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return tmp_path


@needs_jq
def test_the_tuning_step_publishes_the_committed_values(tmp_path: Path):
    """The real file through the real step: this is the knob's only consumer."""
    shutil.copytree(_TUNING.parent, tmp_path / ".github", dirs_exist_ok=True)
    output = tmp_path / "github_output"

    result = _run(_body(_TUNING_STEP), tmp_path, github_output=output)

    assert result.returncode == 0, result.stderr
    live = json.loads(_TUNING.read_text(encoding="utf-8"))["live"]
    assert _outputs(output) == {
        "retries": str(live["retries"]),
        "delay": str(live["retry_delay_seconds"]),
    }


@needs_jq
def test_the_tuning_step_reads_the_tuned_numbers_and_not_a_constant(tmp_path: Path):
    """Tuning the file has to change what the live step is handed."""
    _tuning_tree(tmp_path, {"live": {"retries": 4, "retry_delay_seconds": 5}})
    output = tmp_path / "github_output"

    result = _run(_body(_TUNING_STEP), tmp_path, github_output=output)

    assert result.returncode == 0, result.stderr
    assert _outputs(output) == {"retries": "4", "delay": "5"}


@needs_jq
def test_the_tuning_step_falls_back_when_the_live_block_is_absent(tmp_path: Path):
    """A tuning file that predates the `live` block must not break the nightly."""
    _tuning_tree(tmp_path, {"coverage": {"floor_percent": 55}})
    output = tmp_path / "github_output"

    result = _run(_body(_TUNING_STEP), tmp_path, github_output=output)

    assert result.returncode == 0, result.stderr
    assert _outputs(output) == {"retries": "1", "delay": "60"}


@needs_jq
def test_a_missing_tuning_file_leaves_the_outputs_empty_rather_than_failing(tmp_path: Path):
    """Recorded, not endorsed.

    `jq` writes its error to stderr and the surrounding `echo` still succeeds,
    so the step is green with `retries=` and `delay=` empty. That is survivable
    only because the live step spells its defaults a second time in
    `${RETRIES:-1}`; the test below pins those two spellings together.
    """
    output = tmp_path / "github_output"

    result = _run(_body(_TUNING_STEP), tmp_path, github_output=output)

    assert result.returncode == 0
    assert _outputs(output) == {"retries": "", "delay": ""}


def test_the_tuning_paths_the_step_reads_exist_in_the_committed_file():
    """`// 1` means a renamed key reads as the default instead of failing."""
    filters = _JQ_FILTER.findall(_step(_TUNING_STEP).run or "")
    assert len(filters) == 2, f"expected two jq reads in {_TUNING_STEP!r}, got {filters}"

    document = json.loads(_TUNING.read_text(encoding="utf-8"))
    for expression in filters:
        path = expression.split("//")[0].strip()
        node = document
        for key in path.lstrip(".").split("."):
            assert isinstance(node, dict) and key in node, (
                f"nightly-compliance.yml reads {path} from {_TUNING.name}, which has no such key; "
                "the jq default would be used and the tuning silently ignored"
            )
            node = node[key]
        assert isinstance(node, int) and node >= 0, (
            f"{path} is {node!r}; the live step compares it with `-gt`, which needs an integer"
        )


def test_the_tuning_file_still_names_the_workflow_that_reads_it():
    """The file's `$comment` is the only signpost from the knob to its consumer."""
    document = json.loads(_TUNING.read_text(encoding="utf-8"))
    assert ".github/workflows/nightly-compliance.yml" in document["$comment"]
    assert ".github/auto-qa-tuning.json" in (_step(_TUNING_STEP).run or "")


# --------------------------------------------------------------------------
# Run live suite against real Goodreads endpoints
# --------------------------------------------------------------------------


def _live_stubs(tmp_path: Path, *, fail_until: int) -> tuple[Path, Path, Path]:
    """`pytest` failing the first `fail_until` attempts, plus a recording `sleep`."""
    stubs = _bin(tmp_path)
    attempts = tmp_path / "attempts"
    sleeps = tmp_path / "sleeps"
    _write_stub(
        stubs,
        "pytest",
        f'n=$(cat "{attempts}" 2>/dev/null || echo 0)\n'
        "n=$((n + 1))\n"
        f'echo "$n" > "{attempts}"\n'
        'echo "live output line $n"\n'
        f"[ \"$n\" -gt {fail_until} ]\n",
    )
    _write_stub(stubs, "sleep", _recorder(sleeps))
    return stubs, attempts, sleeps


def _attempts(path: Path) -> int:
    return int(path.read_text(encoding="utf-8")) if path.exists() else 0


def test_a_green_live_run_makes_one_attempt_and_never_sleeps(tmp_path: Path):
    stubs, attempts, sleeps = _live_stubs(tmp_path, fail_until=0)

    result = _run(_body(_LIVE_STEP), tmp_path, path_dirs=[stubs], env={"RETRIES": "1", "DELAY": "60"})

    assert result.returncode == 0, result.stderr
    assert _attempts(attempts) == 1
    assert _argv(sleeps) == []


def test_the_live_step_keeps_the_output_for_the_summary_and_the_issue(tmp_path: Path):
    """`tee live.log` is what the two following steps read; nothing else writes it."""
    stubs, _, _ = _live_stubs(tmp_path, fail_until=0)

    _run(_body(_LIVE_STEP), tmp_path, path_dirs=[stubs], env={"RETRIES": "1", "DELAY": "60"})

    assert (tmp_path / "live.log").read_text(encoding="utf-8") == "live output line 1\n"


def test_a_transient_failure_is_retried_and_does_not_raise_the_alarm(tmp_path: Path):
    """The reason the nightly retries at all: a flaky network is not upstream drift."""
    stubs, attempts, sleeps = _live_stubs(tmp_path, fail_until=1)

    result = _run(_body(_LIVE_STEP), tmp_path, path_dirs=[stubs], env={"RETRIES": "1", "DELAY": "7"})

    assert result.returncode == 0, result.stderr
    assert _attempts(attempts) == 2
    assert [record[1:] for record in _argv(sleeps)] == [["7"]]
    assert "attempt 1 failed; retrying in 7s" in result.stdout


def test_the_retry_budget_bounds_the_attempts(tmp_path: Path):
    """`retries: 2` in the tuning file has to buy exactly one more attempt."""
    stubs, attempts, sleeps = _live_stubs(tmp_path, fail_until=99)

    result = _run(_body(_LIVE_STEP), tmp_path, path_dirs=[stubs], env={"RETRIES": "2", "DELAY": "1"})

    assert result.returncode == 1
    assert _attempts(attempts) == 3
    assert len(_argv(sleeps)) == 2


def test_a_zero_retry_budget_alarms_on_the_first_failure(tmp_path: Path):
    """The other end of the knob: no retry, no sleep, straight to the issue."""
    stubs, attempts, sleeps = _live_stubs(tmp_path, fail_until=99)

    result = _run(_body(_LIVE_STEP), tmp_path, path_dirs=[stubs], env={"RETRIES": "0", "DELAY": "1"})

    assert result.returncode == 1
    assert _attempts(attempts) == 1
    assert _argv(sleeps) == []


def test_a_persistent_failure_fails_the_step_and_says_how_hard_it_tried(tmp_path: Path):
    """The count is in the log a maintainer reads before blaming Goodreads."""
    stubs, _, _ = _live_stubs(tmp_path, fail_until=99)

    result = _run(_body(_LIVE_STEP), tmp_path, path_dirs=[stubs], env={"RETRIES": "1", "DELAY": "1"})

    assert result.returncode == 1
    assert "live suite failed after 2 attempt(s)" in result.stdout


def test_the_live_step_defaults_its_budget_when_the_tuning_step_published_nothing(tmp_path: Path):
    """With `RETRIES`/`DELAY` empty — a missing tuning file — one retry, 60s."""
    stubs, attempts, sleeps = _live_stubs(tmp_path, fail_until=99)

    result = _run(_body(_LIVE_STEP), tmp_path, path_dirs=[stubs], env={"RETRIES": "", "DELAY": ""})

    assert result.returncode == 1
    assert _attempts(attempts) == 2
    assert [record[1:] for record in _argv(sleeps)] == [["60"]]


def test_the_live_run_keeps_the_output_of_a_failing_attempt(tmp_path: Path):
    """A failed run is the one whose output the drift issue quotes."""
    stubs, _, _ = _live_stubs(tmp_path, fail_until=99)

    _run(_body(_LIVE_STEP), tmp_path, path_dirs=[stubs], env={"RETRIES": "0", "DELAY": "1"})

    assert (tmp_path / "live.log").read_text(encoding="utf-8") == "live output line 1\n"


def test_pipefail_is_what_makes_a_red_live_suite_visible_through_tee(tmp_path: Path):
    """Delete that one line and the nightly detects nothing, green, forever.

    GitHub runs a `run:` body under `bash -e` and adds `pipefail` only when the
    step asks for `shell: bash`; this one does not, so it sets the option
    itself. With the line removed the `until` loop reads `tee`'s exit status,
    the first red attempt looks green, and the step succeeds.
    """
    body = _body(_LIVE_STEP)
    assert "set -o pipefail" in body

    stubs, attempts, _ = _live_stubs(tmp_path, fail_until=99)
    without = body.replace("set -o pipefail\n", "", 1)
    assert without != body

    result = _run(without, tmp_path, path_dirs=[stubs], env={"RETRIES": "1", "DELAY": "1"})

    assert result.returncode == 0, "the mutant is supposed to pass; the guard is the line itself"
    assert _attempts(attempts) == 1


def test_the_live_step_runs_the_end_to_end_suite_with_the_opt_in_set():
    """`GOODREADS_LIVE=1` is what un-skips `tests/e2e`; without it this is a no-op."""
    step = _step(_LIVE_STEP)
    assert step.env.get("GOODREADS_LIVE") == "1"
    assert "pytest tests/e2e" in (step.run or "")
    assert _E2E.is_dir()
    guard = (_E2E / "test_smoke_live.py").read_text(encoding="utf-8")
    assert 'os.environ.get("GOODREADS_LIVE") != "1"' in guard


def test_each_tuning_default_is_spelled_the_same_way_in_both_steps():
    """Two copies of each default, one per step, joined by nothing but this test.

    `jq -r '.live.retries // 1'` and `${RETRIES:-1}` both decide what happens
    when the tuning file is silent. Changing one and not the other makes the
    nightly behave differently depending on why the value is missing.
    """
    tuning_body = _step(_TUNING_STEP).run or ""
    live_body = _step(_LIVE_STEP).run or ""

    outputs_by_var = {}
    for variable, expression in _step(_LIVE_STEP).env.items():
        match = _workflow_steps.OUTPUT_REF.search(expression)
        if match:
            outputs_by_var[variable] = match.group(2)
    assert outputs_by_var, "the live step no longer consumes the tuning outputs"

    for variable, output in sorted(outputs_by_var.items()):
        jq_defaults = re.findall(rf"{output}=\$\(jq -r '[^']*//\s*(\d+)'", tuning_body)
        assert len(jq_defaults) == 1, f"no single jq default writes {output}= in {_TUNING_STEP!r}"
        shell_defaults = set(re.findall(rf"\$\{{{variable}:-(\d+)\}}", live_body))
        assert shell_defaults, f"the live step never falls back for ${variable}"
        assert shell_defaults == {jq_defaults[0]}, (
            f"{output} defaults to {jq_defaults[0]} in {_TUNING_STEP!r} but ${variable} "
            f"falls back to {sorted(shell_defaults)} in {_LIVE_STEP!r}"
        )


# --------------------------------------------------------------------------
# Summarize
# --------------------------------------------------------------------------


def _summary(tmp_path: Path, existing: str = "") -> tuple[subprocess.CompletedProcess[str], str]:
    path = tmp_path / "step_summary.md"
    path.write_text(existing, encoding="utf-8")
    result = _run(_body(_SUMMARY_STEP), tmp_path, env={"GITHUB_STEP_SUMMARY": str(path)})
    return result, path.read_text(encoding="utf-8")


def test_the_summary_quotes_the_tail_of_the_live_log_in_a_fenced_block(tmp_path: Path):
    (tmp_path / "live.log").write_text("".join(f"line {i}\n" for i in range(1, 41)), encoding="utf-8")

    result, summary = _summary(tmp_path)

    assert result.returncode == 0, result.stderr
    assert summary.startswith("## Live endpoint check\n\n```\n")
    assert summary.endswith("```\n")
    assert "line 40\n" in summary
    assert "line 11\n" in summary
    assert "line 10\n" not in summary, "the summary is supposed to be the last 30 lines"


def test_the_summary_says_so_when_the_live_step_produced_no_log(tmp_path: Path):
    """The step is `always()`, so it also runs after an install that never got there."""
    result, summary = _summary(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "no output captured" in summary
    assert summary.count("```") == 2


def test_the_summary_appends_rather_than_replacing_what_is_already_there(tmp_path: Path):
    (tmp_path / "live.log").write_text("ok\n", encoding="utf-8")

    _, summary = _summary(tmp_path, existing="## An earlier step\n")

    assert summary.startswith("## An earlier step\n## Live endpoint check\n")


def test_the_summary_runs_even_when_the_live_suite_failed():
    """Without `always()` the summary is missing from exactly the runs that need it."""
    assert _step(_SUMMARY_STEP).if_ == "always()"


# --------------------------------------------------------------------------
# Open or update a drift issue on failure
# --------------------------------------------------------------------------


_RUN_ENV = {
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "Danathar/goodreads-mcp",
    "GITHUB_RUN_ID": "12345",
    "TITLE": _TITLE,
    "GH_TOKEN": "x",
}


def _gh_stub(directory: Path, log: Path, existing: str) -> None:
    _write_stub(
        directory,
        "gh",
        _recorder(log) + f'case "$1 $2" in\n  "issue list") printf \'%s\' "{existing}" ;;\nesac\n',
    )


def _drift(tmp_path: Path, *, existing: str, log_text: str | None = "live output\n"):
    stubs = _bin(tmp_path)
    log = tmp_path / "argv"
    _gh_stub(stubs, log, existing)
    _write_stub(stubs, "date", _recorder(tmp_path / "date-argv") + 'echo "2026-01-02"\n')
    if log_text is not None:
        (tmp_path / "live.log").write_text(log_text, encoding="utf-8")

    result = _run(_body(_ISSUE_STEP), tmp_path, path_dirs=[stubs], env=dict(_RUN_ENV))
    return result, [record[1:] for record in _argv(log)]


def _flag(call: list[str], flag: str) -> str:
    return call[call.index(flag) + 1]


def test_a_first_failure_opens_one_issue_with_the_agreed_title(tmp_path: Path):
    result, calls = _drift(tmp_path, existing="")

    assert result.returncode == 0, result.stderr
    assert [call[:2] for call in calls] == [["issue", "list"], ["issue", "create"]]
    assert _flag(calls[1], "--title") == _TITLE


def test_a_repeat_failure_comments_on_the_open_issue_instead_of_opening_another(tmp_path: Path):
    """Otherwise a week of upstream breakage is seven identical issues."""
    result, calls = _drift(tmp_path, existing="42")

    assert result.returncode == 0, result.stderr
    assert [call[:3] for call in calls] == [["issue", "list", "--state"], ["issue", "comment", "42"]]
    assert not any(call[:2] == ["issue", "create"] for call in calls)


def test_the_search_is_scoped_to_open_issues_with_that_exact_title(tmp_path: Path):
    """A title-only search would match the issue text of anything quoting it."""
    _, calls = _drift(tmp_path, existing="")

    search = calls[0]
    assert _flag(search, "--state") == "open"
    assert _flag(search, "--search") == f"{_TITLE} in:title"
    assert _flag(search, "--jq") == ".[0].number // empty"


def test_the_issue_body_carries_the_run_link_the_date_and_the_live_output(tmp_path: Path):
    _, calls = _drift(tmp_path, existing="", log_text="".join(f"line {i}\n" for i in range(1, 41)))

    body = _flag(calls[1], "--body")
    assert "The nightly live suite failed on 2026-01-02." in body
    assert "Run: https://github.com/Danathar/goodreads-mcp/actions/runs/12345" in body
    assert "line 40" in body
    assert "line 11" in body
    assert "line 10\n" not in body
    assert body.count("```") == 2


def test_the_issue_is_dated_in_utc_to_the_day(tmp_path: Path):
    """The runner's clock is UTC and a bare year would not identify the run."""
    _drift(tmp_path, existing="")

    assert [record[1:] for record in _argv(tmp_path / "date-argv")] == [["-u", "+%Y-%m-%d"]]


def test_the_comment_body_is_the_same_text_as_a_fresh_issue_body(tmp_path: Path):
    """A follow-up comment that dropped the run link would be unactionable."""
    _, opened = _drift(tmp_path, existing="")
    _, commented = _drift(tmp_path, existing="42")

    assert _flag(commented[1], "--body") == _flag(opened[1], "--body")


def test_the_issue_survives_a_run_that_never_wrote_a_log(tmp_path: Path):
    """`live.log` is missing whenever the install step is what failed."""
    result, calls = _drift(tmp_path, existing="", log_text=None)

    assert result.returncode == 0, result.stderr
    assert "The nightly live suite failed on 2026-01-02." in _flag(calls[1], "--body")


def test_the_issue_points_at_a_triage_skill_that_exists(tmp_path: Path):
    """The body's one instruction is to use that skill; a rename orphans it."""
    _, calls = _drift(tmp_path, existing="")

    body = _flag(calls[1], "--body")
    match = re.search(r"`([a-z-]+)` skill", body)
    assert match, "the drift issue no longer names a triage skill"
    assert (_ROOT / ".claude" / "skills" / match.group(1) / "SKILL.md").is_file()


def test_the_drift_issue_is_only_opened_by_the_scheduled_run():
    """A `workflow_dispatch` check is someone watching; it must not file an issue."""
    assert _step(_ISSUE_STEP).if_ == "failure() && github.event_name == 'schedule'"


def test_the_title_the_step_searches_for_is_the_title_it_opens():
    """Both come from one `TITLE` env var; splitting them would defeat the dedupe."""
    step = _step(_ISSUE_STEP)
    assert step.env["TITLE"] == _TITLE
    body = step.run or ""
    assert body.count('"$TITLE"') == 1 and '--title "$TITLE"' in body


# --------------------------------------------------------------------------
# The workflow around the steps
# --------------------------------------------------------------------------


def test_every_step_output_referenced_anywhere_is_written_by_the_step_that_owns_it():
    """A reference to an output no body writes evaluates empty and un-tunes the run."""
    references = set(_workflow_steps.OUTPUT_REF.findall(_WORKFLOW.text))
    assert references, "nightly-compliance.yml no longer wires any step output"

    by_id = {step.id: step for step in _STEPS if step.id}
    for step_id, output in sorted(references):
        assert step_id in by_id, f"reads steps.{step_id}.outputs.{output}, but no step has id {step_id!r}"
        body = by_id[step_id].run or ""
        assert f"{output}=" in body and "GITHUB_OUTPUT" in body, (
            f"steps.{step_id}.outputs.{output} is referenced but step {by_id[step_id].name!r} "
            f"never writes {output}= to $GITHUB_OUTPUT"
        )


def test_the_nightly_runs_on_a_schedule_and_on_demand():
    """A drift detector nobody can trigger by hand is a drift detector you cannot verify."""
    assert re.search(r"^on:\n  schedule:\n    - cron: \"[\d */,-]+\"$", _WORKFLOW.text, re.M)
    assert re.search(r"^  workflow_dispatch:$", _WORKFLOW.text, re.M)


def test_the_nightly_may_write_issues_and_nothing_else():
    """`issues: write` is for the drift issue; a broader grant has no user here."""
    permissions = re.search(r"^permissions:\n((?:  .*\n)+)", _WORKFLOW.text, re.M)
    assert permissions
    granted = dict(
        line.strip().split(": ", 1) for line in permissions.group(1).splitlines() if line.strip()
    )
    assert granted == {"contents": "read", "issues": "write"}


def test_concurrent_nightlies_queue_rather_than_cancelling_each_other():
    """`cancel-in-progress: true` would let a manual run abort the scheduled one mid-suite."""
    assert re.search(r"^concurrency:\n  group: \S+\n  cancel-in-progress: false$", _WORKFLOW.text, re.M)


def test_the_readme_badge_points_at_this_workflow():
    """The badge is how a reader learns the nightly exists at all."""
    readme = _README.read_text(encoding="utf-8")
    assert "actions/workflows/nightly-compliance.yml/badge.svg" in readme
    assert "(.github/workflows/nightly-compliance.yml)" in readme
    name = re.search(r"^name: (.+)$", _WORKFLOW.text, re.M).group(1)
    assert f"[![{name}]" in readme, "the badge label no longer matches the workflow's name:"


def test_every_run_step_in_the_nightly_workflow_is_executed_by_this_file():
    """A new step must be run here, or listed here as deliberately not run."""
    present = _WORKFLOW.run_step_names()
    assert present == _EXECUTED, (
        "nightly-compliance.yml's run: steps and the set this file executes have diverged; "
        f"unrun: {sorted(present - _EXECUTED)}, stale: {sorted(_EXECUTED - present)}"
    )
