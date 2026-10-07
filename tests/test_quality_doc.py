"""`docs/quality.md` says what runs automatically; join that to the workflows.

The page is where a reader goes to learn what guards this repository and what
does not. Its numbers rows were already pinned (`test_coverage_thresholds.py`)
and so were the release refusals it holds (`test_release_docs.py`), but nothing
compared its gate table or its "honest weak spot" prose with the workflow files
themselves. So the page could keep saying the live suite runs only by hand,
and list "a scheduled live run" as an improvement still to make, for weeks
after `nightly-compliance.yml` started running it every day.

These tests make three joins:

- **Every workflow is a gate row or an exemption.** A workflow under
  `.github/workflows/` must be named in the `where` column of the gate table,
  or be listed in `_NOT_A_GATE` below with the reason it is not one. An
  exemption that the table now names, or that names no file, fails too, so the
  list cannot go stale in either direction.
- **The `blocking` column matches the triggers.** A plain `yes` is only true of
  a workflow that runs on `push` or `pull_request`; anything else cannot stop a
  merge. `yes, at release` is only true of `release.yml`.
- **The live suite is described as it runs.** Every workflow that runs on a
  schedule and runs `pytest tests/e2e` is named in "The honest weak spot", its
  row gives the hour its `cron` fires, and "What would actually improve
  quality" does not propose a scheduled live run while one exists.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from tests._workflow_steps import KEY, workflow_files

_ROOT = Path(__file__).resolve().parent.parent
_QUALITY = _ROOT / "docs" / "quality.md"

# Workflows that guard nothing, and so have no row in the gate table. Each
# entry says why; a workflow that starts gating something belongs in the table.
_NOT_A_GATE = {
    "ai-fix.yml": "a human-triggered agent that writes a fix; it checks nothing",
    "labeler.yml": "adds path labels to pull requests; it checks nothing",
    "merge-queue.yml": "inert until a merge queue is enabled, and then a copy of ci.yml's `test` job",
}

# A blocking value of exactly `yes` needs one of these triggers.
_GATING_TRIGGERS = {"push", "pull_request"}

# Words that would mean the improvement list still proposes a scheduled run.
_SCHEDULED_WORDS = re.compile(r"\b(?:schedul\w*|timer|nightly|daily)\b", re.I)

# The `- cron: "M H * * *"` of a daily schedule.
_DAILY_CRON = re.compile(r"""^\s*-\s*cron:\s*["'](\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*["']""", re.M)


def _section(text: str, heading: str) -> str:
    """The body under `## heading`, up to the next `## `."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"docs/quality.md has no '## {heading}' section"
    return match.group(1)


def _gate_rows(text: str) -> list[tuple[str, str, str]]:
    """`(gate, where, blocking)` for each row of the table under "What runs automatically"."""
    rows = []
    for line in _section(text, "What runs automatically").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells == ["gate", "where", "blocking"] or set("".join(cells)) <= {"-"}:
            continue
        assert len(cells) == 3, f"gate table row does not have three cells: {line!r}"
        rows.append((cells[0], cells[1], cells[2]))
    assert rows, "the gate table under 'What runs automatically' has no rows"
    return rows


def _named_workflows(where: str) -> set[str]:
    return set(re.findall(r"`([A-Za-z0-9_.-]+\.ya?ml)`", where))


def _triggers(text: str) -> set[str]:
    """The event names directly under the top-level `on:` key."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if (m := KEY.match(line)) and m["key"] == "on" and not m["indent"]]
    assert len(starts) == 1, "workflow has no single top-level on: key"
    events: set[str] = set()
    for line in lines[starts[0] + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        match = KEY.match(line)
        if match and len(match["indent"]) == 2:
            events.add(match["key"])
    assert events, "workflow's on: key has no events (flow-style on: is not read here)"
    return events


@pytest.fixture(scope="module")
def page() -> str:
    return _QUALITY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflows() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in workflow_files()}


def _live_runners(workflows: dict[str, str]) -> dict[str, str]:
    """Workflows that run the live suite on a schedule."""
    return {
        name: text
        for name, text in workflows.items()
        if "schedule" in _triggers(text) and re.search(r"\bpytest\s+tests/e2e\b", text)
    }


def test_every_workflow_is_a_gate_row_or_an_exemption(page, workflows):
    named = set().union(*(_named_workflows(where) for _, where, _ in _gate_rows(page)))
    missing = set(workflows) - named - set(_NOT_A_GATE)
    assert not missing, (
        f"docs/quality.md's gate table does not name {sorted(missing)}; add a row saying what it "
        "guards and whether it blocks, or list it in _NOT_A_GATE with the reason it guards nothing"
    )
    unknown = named - set(workflows)
    assert not unknown, f"docs/quality.md's gate table names workflows that do not exist: {sorted(unknown)}"


def test_every_exemption_is_still_needed(page, workflows):
    named = set().union(*(_named_workflows(where) for _, where, _ in _gate_rows(page)))
    for name in _NOT_A_GATE:
        assert name in workflows, f"_NOT_A_GATE lists {name}, which is not a workflow any more"
        assert name not in named, f"docs/quality.md now has a row for {name}; drop it from _NOT_A_GATE"


def test_blocking_column_matches_the_triggers(page, workflows):
    for gate, where, blocking in _gate_rows(page):
        for name in _named_workflows(where):
            if name not in workflows:
                continue
            events = _triggers(workflows[name])
            if blocking == "yes":
                assert events & _GATING_TRIGGERS, (
                    f"docs/quality.md says {gate!r} in {name} blocks, but {name} runs on "
                    f"{sorted(events)}, none of which can stop a merge"
                )
            if blocking.startswith("yes, at release"):
                assert name == "release.yml", f"{gate!r} is blocking at release, but runs in {name}"
            if not blocking.startswith("yes"):
                assert not events & _GATING_TRIGGERS, (
                    f"docs/quality.md says {gate!r} in {name} does not block, but it runs on "
                    f"{sorted(events & _GATING_TRIGGERS)}"
                )


def test_a_live_suite_runner_exists(workflows):
    """Guards the next tests from passing on an empty set."""
    assert _live_runners(workflows), "no scheduled workflow runs `pytest tests/e2e` any more"


def test_the_weak_spot_names_every_scheduled_live_run(page, workflows):
    weak_spot = _section(page, "The honest weak spot")
    for name in _live_runners(workflows):
        assert f"`{name}`" in weak_spot, (
            f"{name} runs the live suite on a schedule, but 'The honest weak spot' does not name it, "
            "so the page reads as if nothing runs the live suite unattended"
        )


def test_the_live_run_row_gives_the_cron_hour(page, workflows):
    rows = _gate_rows(page)
    for name, text in _live_runners(workflows).items():
        crons = _DAILY_CRON.findall(text)
        assert len(crons) == 1, f"{name} no longer has exactly one daily cron line"
        minute, hour = crons[0]
        when = f"daily at {int(hour):02d}:{int(minute):02d} UTC"
        matching = [gate for gate, where, _ in rows if name in _named_workflows(where)]
        assert len(matching) == 1, f"docs/quality.md has {len(matching)} gate rows naming {name}"
        assert when in matching[0], f"{name}'s row should say {when!r}; it says {matching[0]!r}"


def test_the_improvement_list_does_not_propose_a_scheduled_run_that_exists(page, workflows):
    if not _live_runners(workflows):
        pytest.skip("no scheduled live run exists")
    proposals = _section(page, "What would actually improve quality")
    items = re.findall(r"^\d+\.\s.*?(?=^\d+\.\s|^\S|\Z)", proposals, re.M | re.S)
    assert items, "'What would actually improve quality' has no numbered list"
    for item in items:
        assert not _SCHEDULED_WORDS.search(item), (
            f"the improvement list still proposes a scheduled run, which already exists: {item.strip()!r}"
        )
