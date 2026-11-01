"""`docs/ai-ops-runbook.md` quotes the titles of the issues workflows file; the workflows own them.

The runbook tells the maintainer to look for an open issue with an exact title:
the nightly drift issue `nightly-compliance.yml` files, and the CI-failure issue
`auto-issues.yml` files. Each workflow creates its issue (and finds an existing
one) from its `TITLE` variable. Reword either side and the runbook sends the
maintainer to look for an issue that is never filed, with nothing failing.
This test compares the strings; it does not check that the file exists.

The runbook's "A release run refused" table is held to `release.yml` the same
way: the steps it names, their order, the messages it quotes, the schedule and
the inputs it tells the maintainer to use.
"""

from __future__ import annotations

from pathlib import Path
import re

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_RUNBOOK = _ROOT / "docs" / "ai-ops-runbook.md"


def _title(workflow: str) -> str:
    match = re.search(r'^\s*TITLE: "([^"]+)"\s*$', (_WORKFLOWS / workflow).read_text(encoding="utf-8"), re.M)
    assert match, f"{workflow} no longer sets TITLE in the form this test reads"
    return match.group(1)


def test_runbook_quotes_the_titles_the_workflows_file():
    nightly = _title("nightly-compliance.yml")
    ci_failure = _title("auto-issues.yml")

    runbook = _RUNBOOK.read_text(encoding="utf-8")
    assert f"> Title: `{nightly}`" in runbook
    assert f"opens an issue titled `{ci_failure}`" in runbook
    commands = re.findall(r'select\(\.title == "([^"]+)"', runbook)
    assert commands == [nightly, ci_failure]


# --------------------------------------------------------------------------
# "A release run refused": the table of steps, in order, and their messages
# --------------------------------------------------------------------------
#
# The section tells a maintainer which step of the `release` job stopped a run
# and what the message there means. Each row names steps by their exact names
# (the `gh run view ... --jq` command above it prints `.steps[].name`) and
# quotes what the step prints. Nothing read the table: rename a step, add a
# refusal, reword a message, or move a step, and the runbook sends the
# maintainer to a row that matches nothing in the failed run.

_RELEASE = _WORKFLOWS / "release.yml"
_TAG_STEP = "Tag the approved commit"

# What the table's quoted messages assume for the step's variables: the
# default branch is `main` (the table's first row says so), and every other
# variable a message names is a literal in the step's own `env:`.
_DEFAULT_BRANCH = "main"


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"the runbook has no `## {heading}` section"
    return match.group(1)


def _refusal_rows() -> list[tuple[list[str], list[str]]]:
    """Each table row as (step names, backticked strings in the message cell)."""
    section = _section(_RUNBOOK.read_text(encoding="utf-8"), "A release run refused")
    rows = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("| `") or len(cells) != 3:
            continue
        steps = [name.strip().strip("`") for name in cells[0].split(" / ")]
        rows.append((steps, re.findall(r"`([^`]+)`", cells[1])))
    assert len(rows) >= 5, f"read only {len(rows)} rows out of the release table"
    return rows


def _release_job() -> _workflow_steps.Workflow:
    return _workflow_steps.Workflow(_RELEASE, job="release")


def _rendered(step: _workflow_steps.Step) -> str:
    """The step's shell with the variables a quoted message names filled in."""
    body = step.run or ""
    values = {name: value for name, value in step.env.items() if "${{" not in value}
    values["DEFAULT"] = _DEFAULT_BRANCH
    for name, value in values.items():
        body = re.sub(rf"\$\{{?{name}\}}?(?![A-Za-z0-9_])", value, body)
    return body


def _stops_the_run(step: _workflow_steps.Step) -> bool:
    """A step that refuses with an error, or ends the run green with nothing tagged."""
    body = step.run or ""
    return "::error::" in body or 'echo "go=false"' in body


def test_the_release_table_names_release_job_steps_in_job_order():
    order = [step.name for step in _release_job().steps]
    named = [name for steps, _ in _refusal_rows() for name in steps]
    missing = [name for name in named if name not in order]
    assert not missing, f"the runbook names steps the release job does not have: {missing}"
    positions = [order.index(name) for name in named]
    assert positions == sorted(positions), (
        f"the runbook says the steps are in order, but release.yml runs them as {order}"
    )


def test_every_step_that_stops_a_release_before_the_tag_has_a_row():
    steps = _release_job().steps
    order = [step.name for step in steps]
    assert _TAG_STEP in order, f"release.yml's release job has no step named {_TAG_STEP!r}"
    before_tag = steps[: order.index(_TAG_STEP)]
    named = {name for row_steps, _ in _refusal_rows() for name in row_steps}
    unlisted = [step.name for step in before_tag if _stops_the_run(step) and step.name not in named]
    assert not unlisted, f"these steps stop a release and have no row in the runbook: {unlisted}"
    listed = [step.name for step in before_tag if step.name in named]
    passive = [name for name in listed if not _stops_the_run(_release_job().step(name))]
    assert not passive, f"the runbook lists steps that cannot stop a release: {passive}"


def test_every_quoted_message_is_printed_by_its_rows_steps():
    job = _release_job()
    for steps, quoted in _refusal_rows():
        bodies = "\n".join(_rendered(job.step(name)) for name in steps)
        for message in quoted:
            pieces = [piece.strip() for piece in message.split("...")]
            where = 0
            for piece in pieces:
                found = bodies.find(piece, where)
                assert found >= 0, f"{steps} print nothing like `{message}` (missing {piece!r})"
                where = found + len(piece)


def test_the_summary_messages_are_the_steps_own():
    job = _release_job()
    section = _section(_RUNBOOK.read_text(encoding="utf-8"), "A release run refused")
    assert "monthly releases are off" in section
    assert "Monthly releases are off." in _rendered(job.step("Check the schedule is enabled"))
    assert "AUTO_RELEASE_ENABLED" in section
    assert "vars.AUTO_RELEASE_ENABLED" in job.step("Check the schedule is enabled").env["OPTED_IN"]
    assert "Nothing under `goodreads_mcp/` changed" in section
    assert "Nothing under \\`goodreads_mcp/\\` changed" in job.step("Decide whether to release").run
    assert "with `force`" in section
    assert job.step("Decide whether to release").env["FORCE"] == "${{ inputs.force }}"


def test_the_release_section_names_the_workflow_its_schedule_and_the_tag_step():
    workflow = _RELEASE.read_text(encoding="utf-8")
    section = re.sub(r"\s+", " ", _section(_RUNBOOK.read_text(encoding="utf-8"), "A release run refused")).strip()
    name = re.search(r"^name: (.+)$", workflow, re.M).group(1).strip()
    assert section.startswith(f"`{name}` runs at "), f"release.yml is named {name!r}"
    crons = re.findall(r'^\s*- cron: "(.+)"$', workflow, re.M)
    assert len(crons) == 1, f"release.yml has {len(crons)} cron lines"
    minute, hour, day, month, weekday = crons[0].split()
    assert (month, weekday, day) == ("*", "*", "1"), f"the cron {crons[0]!r} is no longer the 1st of each month"
    assert f"runs at {int(hour):02d}:{int(minute):02d} UTC on the 1st (or by hand)" in section
    assert re.search(r"^  workflow_dispatch:$", workflow, re.M), "release.yml can no longer be run by hand"
    assert re.search(r"^      dry_run:$", workflow, re.M), "release.yml has no dry_run input"
    assert "-f dry_run=true" in section
    assert f"failed after `{_TAG_STEP}`" in section
