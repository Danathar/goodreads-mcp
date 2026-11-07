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

"Dependabot action updates" is held to `.github/dependabot.yml` and to the
workflows that hold `contents: write`, and "A scheduled run did not fire" to
the workflows that carry a `schedule:` trigger, their cron times and the
dispatch inputs its commands pass.

"`ai-fix.yml` runs" is held to that workflow's steps and job condition,
"`guard-bash` refusals" to `.claude/settings.json` and to what the hook prints
when it is handed the section's own commands, and "Agent audit trail is red" to
the schedule, the boundary paths, the caps and the exits in `agent-audit.yml`.

"An agent PR misbehaves" is held to the app login and the signature line
`agent-audit.yml` selects agent pull requests by, to `.github/rulesets/main.json`,
to the hold and Tier 2 rules it cites, and to the claim that no code here reads
the pause label. Its listing command is run over sample pull requests. The
`<role>/<slug>` branch form is Hive's and nothing in this repository reads it.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

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


# --------------------------------------------------------------------------
# "Dependabot action updates": what dependabot.yml asks for
# --------------------------------------------------------------------------
#
# The section tells a maintainer what a normal Dependabot pull request looks
# like (one a week, grouped, prefixed `ci`) and which workflows' bumps need the
# upstream release notes read because they can write to the repository. Change
# the cadence, drop the group, or grant a third workflow `contents: write`, and
# the runbook describes a different repository with nothing failing.

_DEPENDABOT = _ROOT / ".github" / "dependabot.yml"
_CADENCE = {"daily": "a day", "weekly": "a week", "monthly": "a month"}


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_the_dependabot_section_states_what_dependabot_yml_asks_for():
    config = _DEPENDABOT.read_text(encoding="utf-8")
    section = _flat(_section(_RUNBOOK.read_text(encoding="utf-8"), "Dependabot action updates"))
    assert section.startswith("[`.github/dependabot.yml`](../.github/dependabot.yml) asks for one grouped pull request ")
    interval = re.search(r"^      interval: (\w+)\s*$", config, re.M)
    assert interval and interval.group(1) in _CADENCE, "dependabot.yml no longer sets a schedule interval this test reads"
    assert f"one grouped pull request {_CADENCE[interval.group(1)]}," in section
    assert re.search(r'^    groups:\n      [\w-]+:\n        patterns:\n          - "\*"$', config, re.M), (
        "dependabot.yml no longer groups every action into one pull request"
    )
    assert "covering every GitHub Action the workflows `uses:`" in section
    prefix = re.search(r'^      prefix: "([^"]+)"$', config, re.M)
    assert prefix, "dependabot.yml no longer sets a commit-message prefix"
    assert f"with commit prefix `{prefix.group(1)}`." in section
    assert "(`tests/test_action_pins.py` fails the PR if not)" in section
    assert (_ROOT / "tests" / "test_action_pins.py").is_file()


def _grants_contents_write(path: Path) -> bool:
    return any(
        line.split("#", 1)[0].strip() == "contents: write"
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def test_the_dependabot_section_names_every_workflow_that_can_write_contents():
    section = _flat(_section(_RUNBOOK.read_text(encoding="utf-8"), "Dependabot action updates"))
    match = re.search(r"((?:`[\w.-]+\.ya?ml`(?:, | and )?)+) hold `contents: write`", section)
    assert match, "the section no longer says which workflows hold `contents: write`"
    named = sorted(re.findall(r"`([\w.-]+\.ya?ml)`", match.group(1)))
    writers = sorted(p.name for p in _workflow_steps.workflow_files() if _grants_contents_write(p))
    assert named == writers, f"the workflows that hold `contents: write` are {writers}, the runbook names {named}"


# --------------------------------------------------------------------------
# "A scheduled run did not fire": the scheduled workflows and their times
# --------------------------------------------------------------------------
#
# The section lists the scheduled workflows with their times, then gives a
# `gh run list`, a `gh workflow enable` and a `gh workflow run` for each. Add a
# fourth schedule, move a cron, or drop a dispatch input, and the maintainer
# checks the wrong hour, misses a workflow, or runs a command GitHub refuses.

_COUNT = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def _scheduled() -> dict[str, list[str]]:
    """Each workflow with a `schedule:` trigger, and its cron expressions."""
    found = {}
    for path in _workflow_steps.workflow_files():
        text = path.read_text(encoding="utf-8")
        if re.search(r"^  schedule:\s*$", text, re.M):
            found[path.name] = [cron for _, cron in re.findall(r"""^\s*- cron: (["'])(.+?)\1""", text, re.M)]
    return found


def _stated_time(cron: str) -> str:
    """How the runbook words a cron: `daily HH:MM UTC` or `HH:MM UTC on the 1st`."""
    minute, hour, day, month, weekday = cron.split()
    clock = f"{int(hour):02d}:{int(minute):02d} UTC"
    if (day, month, weekday) == ("*", "*", "*"):
        return f"daily {clock}"
    assert (day, month, weekday) == ("1", "*", "*"), f"this test does not word the cron {cron!r}"
    return f"{clock} on the 1st"


def _missed_run_section() -> str:
    return _section(_RUNBOOK.read_text(encoding="utf-8"), "A scheduled run did not fire")


def test_the_missed_run_section_lists_every_schedule_at_its_time():
    scheduled = _scheduled()
    section = _flat(_missed_run_section())
    match = re.search(r"^(\w+) workflows are scheduled: (.+?)\. ", section)
    assert match, "the section no longer opens with the list of scheduled workflows"
    listed = dict(re.findall(r"`([\w.-]+\.ya?ml)` \(([^)]+)\)", match.group(2)))
    assert _COUNT.get(match.group(1)) == len(listed), f"the section says {match.group(1)} and lists {sorted(listed)}"
    assert sorted(listed) == sorted(scheduled), f"the scheduled workflows are {sorted(scheduled)}"
    for name, crons in scheduled.items():
        assert len(crons) == 1, f"{name} has {len(crons)} cron lines"
        assert listed[name] == _stated_time(crons[0]), f"{name} runs at {crons[0]!r}"


def test_the_missed_run_commands_cover_every_scheduled_workflow():
    scheduled = sorted(_scheduled())
    section = _missed_run_section()
    assert sorted(re.findall(r"gh run list --workflow ([\w.-]+) --event schedule", section)) == scheduled
    enable = re.search(r"`gh workflow enable ([\w.-]+)` \(or ((?:`[\w.-]+`(?:, or )?)+)\)", _flat(section))
    assert enable, "the section no longer gives the `gh workflow enable` command"
    assert sorted([enable.group(1), *re.findall(r"`([\w.-]+)`", enable.group(2))]) == scheduled
    dispatched = re.findall(r"^gh workflow run ([\w.-]+) --ref main((?: -f \w+=\S+)*)", section, re.M)
    assert sorted(name for name, _ in dispatched) == scheduled
    for name, flags in dispatched:
        text = (_WORKFLOWS / name).read_text(encoding="utf-8")
        assert re.search(r"^  workflow_dispatch:", text, re.M), f"{name} cannot be run by hand"
        for field in re.findall(r"-f (\w+)=", flags):
            assert re.search(rf"^      {field}:$", text, re.M), f"{name} has no workflow_dispatch input {field!r}"


def test_the_missed_run_dispatch_notes_are_the_workflows_own():
    section = _missed_run_section()
    drift = "\n".join(_workflow_steps.jobs((_WORKFLOWS / "nightly-compliance.yml").read_text(encoding="utf-8"))[0]["report"])
    assert "# live suite; opens no drift issue" in section
    assert "github.event_name == 'schedule'" in drift, "a dispatched nightly run can now open the drift issue"
    assert "# read-only; blank since means the last 31 days" in section
    audit = (_WORKFLOWS / "agent-audit.yml").read_text(encoding="utf-8")
    assert re.search(r"""if \[ -z "\$SINCE" \]; then\n\s+SINCE="\$\(date -u -d '31 days ago' \+%F\)\"""", audit)
    assert re.search(r"^  contents: read\n  pull-requests: read\n", audit, re.M), "agent-audit.yml is no longer read-only"
    workflow = _RELEASE.read_text(encoding="utf-8")
    name = re.search(r"^name: (.+)$", workflow, re.M).group(1).strip()
    assert f"**A green `{name}` run that tagged nothing.**" in section
    assert "[A release run refused](#a-release-run-refused)" in section
    for step in re.findall(r"`([A-Z][^`]+)` and `([A-Z][^`]+)` rows", section)[0]:
        _release_job().step(step)


# --------------------------------------------------------------------------
# "`ai-fix.yml` runs": what a grey, green or red run means
# --------------------------------------------------------------------------
#
# `tests/test_ai_fix_workflow.py` runs the workflow's steps; nothing read the
# page that tells a maintainer what those runs mean. The sentence that matters
# most is "No code is changed by it": wire the agent step up and the section
# still says so, about a job that holds `contents: write`.

_AI_FIX = _WORKFLOWS / "ai-fix.yml"
_AI_FIX_STEPS = [
    "Check requester is a maintainer",
    "",  # the checkout, which has no name
    "Acknowledge the request",
    "Agent step (not configured)",
]


def _ai_fix_section() -> str:
    return _section(_RUNBOOK.read_text(encoding="utf-8"), "`ai-fix.yml` runs")


def test_the_ai_fix_section_names_the_two_requests_the_job_accepts():
    section = _flat(_ai_fix_section())
    label, comment = re.search(r"applies the `([^`]+)` label or comments `([^`]+)`", section).groups()
    text = _AI_FIX.read_text(encoding="utf-8")
    assert f"github.event.label.name == '{label}'" in text
    assert f"startsWith(github.event.comment.body, '{comment}')" in text
    assert f"(the Hive app applies `{label}` to the issues it opens)" in section


def test_the_ai_fix_section_says_what_the_job_does_and_it_still_does_only_that():
    section = _flat(_ai_fix_section())
    assert "only gates the request, posts an acknowledgement comment and writes a summary" in section
    assert "No code is changed by it." in section
    workflow = _workflow_steps.Workflow(_AI_FIX)
    assert [step.name for step in workflow.steps] == _AI_FIX_STEPS, "the job gained or lost a step; reread the section"
    agent_step = re.search(r"its agent step is `([^`]+)`", section).group(1)
    assert workflow.steps[-1].name == agent_step
    assert "gh issue comment" in workflow.step("Acknowledge the request").run
    assert "GITHUB_STEP_SUMMARY" in workflow.step(agent_step).run


def test_the_ai_fix_grep_finds_the_line_the_section_relies_on():
    section = _ai_fix_section()
    pattern, path = re.search(r'^grep -n "([^"]+)" (\S+)$', section, re.M).groups()
    assert (_ROOT / path) == _AI_FIX
    assert pattern in _AI_FIX.read_text(encoding="utf-8")
    assert re.search(r"^  contents: write$", _AI_FIX.read_text(encoding="utf-8"), re.M)
    assert "That job holds `contents: write`" in _flat(section)


def test_the_ai_fix_run_colours_match_the_condition_and_the_gate():
    section = _flat(_ai_fix_section())
    text = _AI_FIX.read_text(encoding="utf-8")
    assert "The event came from a bot" in section
    assert "github.event.sender.type != 'Bot'" in text
    stated = re.search(r"someone who is not the (\w+), a (\w+) or a (\w+)\.", section).groups()
    allowed = re.findall(r"author_association == '(\w+)'", text)
    assert sorted(word.upper() for word in stated) == sorted(allowed)

    start, end = re.search(r"\*\*Red\*\* with `(.+?) \.\.\. (.+?)`", section).groups()
    gate = _workflow_steps.Workflow(_AI_FIX).step("Check requester is a maintainer").run
    assert re.search(rf"{re.escape(start)}.*{re.escape(end)}", gate), "the refusal no longer reads as the runbook quotes it"
    assert "collaborators/<actor>/permission --jq .permission" in section
    assert "collaborators/$ACTOR/permission" in gate and "--jq '.permission'" in gate


# --------------------------------------------------------------------------
# "`guard-bash` refusals": how the hook is wired and what it prints
# --------------------------------------------------------------------------
#
# The section gives a maintainer a command to reproduce a refusal and a list of
# what the hook refuses. These tests hand the hook the section's own commands.

_SETTINGS = _ROOT / ".claude" / "settings.json"
_GUARD = _ROOT / ".claude" / "hooks" / "guard-bash.py"

# One command for each thing the section tells an agent to rewrite away.
_REFUSED_CONSTRUCTS = {
    "a path outside `tests/`": ("pytest /etc/passwd", "outside tests/"),
    "a loader option": ("pytest -p evil tests", "pytest plugin"),
    "a redirection": ("pytest tests > out", "redirection"),
    "a variable assignment in front of the verb": ("FOO=1 pytest tests", "`FOO=...` in front of `pytest`"),
}


def _guard_section() -> str:
    return _section(_RUNBOOK.read_text(encoding="utf-8"), "`guard-bash` refusals")


def _ask_guard(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_GUARD)], input=stdin, capture_output=True, text=True, cwd=_ROOT, timeout=30, check=False
    )


def _denial(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, "the section says the hook exits 0 either way"
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    return output["permissionDecisionReason"]


def _command(command: str) -> str:
    return json.dumps({"tool_input": {"command": command}, "cwd": "."})


def test_the_guard_is_registered_where_the_section_says():
    section = _flat(_guard_section())
    assert "is registered as a `PreToolUse` hook on `Bash` in" in section
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in settings["hooks"]["PreToolUse"]
        if entry["matcher"] == "Bash"
        for hook in entry["hooks"]
    ]
    assert any(".claude/hooks/guard-bash.py" in command for command in commands), commands


def test_the_guard_reproduction_prints_the_denial_the_section_describes():
    section = _guard_section()
    payload, script = re.search(r"^echo '(\{.+\})' \| python3 (\S+)$", section, re.M).groups()
    assert (_ROOT / script) == _GUARD
    reason = _denial(_ask_guard(payload))
    flat = _flat(section)
    assert 'prints a JSON `permissionDecision: "deny"`' in flat
    stated = re.search(r"\(here, `([^`]+)` (imports a module as a pytest plugin)\)", flat).groups()
    assert stated[0] in reason and stated[1] in reason


def test_the_section_s_own_pytest_command_is_allowed_and_prints_nothing():
    section = _guard_section()
    command = re.search(r"^(pytest \S+ -q)$", section, re.M).group(1)
    result = _ask_guard(_command(command))
    assert "An allowed command prints nothing." in _flat(section)
    assert (result.returncode, result.stdout) == (0, "")


def test_every_construct_the_section_names_is_refused():
    section = _flat(_guard_section())
    listed = re.search(r"without the construct it names \((.+?)\)\.", section).group(1)
    assert listed == "a path outside `tests/`, a loader option, a redirection, a variable assignment in front of the verb"
    for construct, (command, words) in _REFUSED_CONSTRUCTS.items():
        assert construct in listed
        assert words in _denial(_ask_guard(_command(command))), command


def test_the_failed_closed_message_is_the_hook_s_own():
    section = _flat(_guard_section())
    assert "a message says `guard-bash.py failed (...)`, the hook failed closed" in section
    assert _denial(_ask_guard("pytest {")).startswith("guard-bash.py failed (")


def test_the_table_of_spellings_is_in_the_file_the_section_names():
    section = _flat(_guard_section())
    path = re.search(r"The table of denied and allowed spellings is in `([^`]+)`", section).group(1)
    text = (_ROOT / path).read_text(encoding="utf-8")
    assert re.search(r"^_DENIED = \[$", text, re.M) and re.search(r"^_PERMITTED = \[$", text, re.M)
    assert f"pytest {path} -q" in _guard_section()


# --------------------------------------------------------------------------
# "Agent audit trail is red": when the audit runs and what fails it
# --------------------------------------------------------------------------
#
# `tests/test_agent_audit_workflow.py` runs the job's jq. This reads the page
# that tells a maintainer what a red run can mean, which is only useful if it
# lists every way the run can go red.

_AUDIT = _WORKFLOWS / "agent-audit.yml"
_AUDIT_STEP = "Audit merged agent pull requests"


def _audit_section() -> str:
    return _flat(_section(_RUNBOOK.read_text(encoding="utf-8"), "Agent audit trail is red"))


def _audit_body() -> str:
    return _workflow_steps.Workflow(_AUDIT).step(_AUDIT_STEP).run


def test_the_audit_section_states_the_schedule_and_the_hand_run():
    section = _audit_section()
    (cron,) = _scheduled()["agent-audit.yml"]
    assert f"runs monthly ({_stated_time(cron)}) or by hand" in section
    assert re.search(r"^  workflow_dispatch:", _AUDIT.read_text(encoding="utf-8"), re.M)


def test_the_audit_section_names_the_boundary_the_job_tests():
    section = _audit_section()
    body = _audit_body()
    assert "touched `.claude/settings.json` or `.claude/hooks/**`" in section
    assert r'def boundary: test("^(\\.claude/settings\\.json$|\\.claude/hooks/)");' in body
    assert "was merged by a bot, an app or an unknown account" in section
    assert 'endswith("[bot]") or startswith("app/")' in body
    assert "(.mergedBy != null) and (.mergedBy.is_bot | not)" in body


def test_the_audit_fails_for_the_one_finding_the_section_names():
    section = _audit_section()
    assert "It fails for one finding:" in section
    assert "are listed in the summary but do not fail it" in section
    body = _audit_body()
    assert len(re.findall(r"\bexit 1\b", body)) == 1
    assert 'select((.boundary | length) > 0 and (.merged_by_human | not))' in body


def test_the_audit_section_lists_every_way_the_step_stops_with_exit_2():
    section = _audit_section()
    body = _audit_body()
    stated = re.search(r"It also stops with exit 2 when (.+?)\. ```", section).group(1)
    assert "`since` is not a real `YYYY-MM-DD` date" in stated
    limit = re.search(r"^\s*limit=(\d+)$", body, re.M).group(1)
    assert f"the window holds {limit} or more merged pull requests" in stated
    commits, files = re.search(r"reaches GitHub\'s (\d+)-commit or (\d+)-file list cap", stated).groups()
    assert f'"$(jq length <<<"$commits")" -ge {commits}' in body
    assert f'"$(jq length <<<"$files")" -ge {files}' in body
    # Two checks of `since`, the window cap, and the per-pull-request cap.
    assert len(re.findall(r"\bexit 2\b", body)) == 4, "a new way to stop needs a line in the section"


def test_the_audit_section_states_the_merge_commit_rule_the_job_applies():
    section = _audit_section()
    body = _audit_body()
    assert "only on a pull request into `main`" in section
    assert '$base == "main"' in body
    assert "only when every parent after the first is outside the pull request\'s own commits" in section
    assert "all(.parents[1:][]; IN($own[]) | not)" in body


def test_the_audit_advice_uses_inputs_and_steps_that_exist():
    section = _audit_section()
    assert "re-run with a later `since`" in section
    assert "The workflow takes no end date." in section
    text = _AUDIT.read_text(encoding="utf-8")
    inputs = re.search(r"^    inputs:\n((?:      .*\n)+)", text, re.M).group(1)
    assert re.findall(r"^      (\w+):$", inputs, re.M) == ["since"]
    assert "[An agent PR misbehaves](#an-agent-pr-misbehaves), step 5" in section
    misbehaves = _section(_RUNBOOK.read_text(encoding="utf-8"), "An agent PR misbehaves")
    assert re.search(r"^5\. \*\*Confirm `main` could not have been written to", misbehaves, re.M)
    assert "look at what merged recently" in _flat(misbehaves)


# --------------------------------------------------------------------------
# "An agent PR misbehaves": who opens agent pull requests, and what stops one
# --------------------------------------------------------------------------
#
# The section tells a maintainer how to find agent pull requests and what the
# ruleset will and will not stop. Both are only useful if they match the login
# and signature the audit selects by and the rules the committed ruleset holds.

_RULESET = _ROOT / ".github" / "rulesets" / "main.json"


def _misbehaves_section() -> str:
    return _section(_RUNBOOK.read_text(encoding="utf-8"), "An agent PR misbehaves")


def _audit_logins() -> list[str]:
    match = re.search(r"\(\.author\.login \| IN\(([^)]*)\)\)", _audit_body())
    assert match, "agent-audit.yml no longer selects the Hive app by login in the form this test reads"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_the_misbehaving_pr_section_names_the_app_the_audit_selects():
    section = _flat(_misbehaves_section())
    gh_login, rest_login = _audit_logins()
    assert gh_login.startswith("app/") and rest_login == gh_login.removeprefix("app/") + "[bot]"
    assert f"opened by the Hive GitHub App (author `{gh_login}`) or by `Danathar`" in section
    assert f"gh pr list --author {gh_login} --state open" in section
    assert f"the Hive app (`{gh_login.removeprefix('app/')}`)" in section


def test_the_misbehaving_pr_section_states_the_signature_the_audit_reads():
    section = _flat(_misbehaves_section())
    body = _audit_body()
    assert "each body ends with a `— hive: agent=...` line" in section
    assert 'startswith("— hive:")' in body
    assert 'test("(^| )agent=[^ ]+")' in body
    assert "it is what `agent-audit.yml` reads when the author alone cannot tell" in section
    assert "The first command leaves out the agent pull requests opened under `Danathar`." in section
    assert re.search(r"\(\.author\.login \| IN\([^)]*\)\)\s+or \(signature != null\)", body)


def _listing_filter() -> tuple[list[str], str]:
    match = re.search(
        r"^gh pr list --state open --json ([a-zA-Z,]+) --jq '([^']+)'$", _misbehaves_section(), re.M
    )
    assert match, "the section no longer lists open agent pull requests by their signature line"
    return match.group(1).split(","), match.group(2)


@pytest.mark.skipif(shutil.which("jq") is None, reason="runs the section's --jq filter with jq")
def test_the_signature_listing_finds_agent_prs_under_either_login():
    fields, jq_filter = _listing_filter()
    prs = [
        {"number": 1, "author": {"login": "Danathar"}, "headRefName": "docs/x",
         "body": "Why.\n\n— hive: agent=docs backend=claude model=m"},
        {"number": 2, "author": {"login": "app/danathar-atomic-hive"}, "headRefName": "quality/test-y",
         "body": "— hive: agent=quality backend=claude model=m\n\nmore text"},
        {"number": 3, "author": {"login": "dependabot[bot]"}, "headRefName": "dependabot/x", "body": "Bumps x"},
        {"number": 4, "author": {"login": "Danathar"}, "headRefName": "main-fix", "body": None},
        {"number": 5, "author": {"login": "Danathar"}, "headRefName": "notes",
         "body": "Quotes `— hive: agent=` inside a line"},
        {"number": 6, "author": {"login": "Danathar"}, "headRefName": "notes-2",
         "body": "Text.\n\n— hive: note, not a signature"},
    ]
    for pr in prs:
        assert set(pr) == set(fields), "the sample must carry exactly the fields the command asks gh for"
    result = subprocess.run(
        ["jq", "-c", jq_filter], input=json.dumps(prs), capture_output=True, text=True, check=True
    )
    listed = [json.loads(line) for line in result.stdout.splitlines()]
    assert listed == [
        {"number": 1, "author": "Danathar", "branch": "docs/x"},
        {"number": 2, "author": "app/danathar-atomic-hive", "branch": "quality/test-y"},
    ]


def test_the_misbehaving_pr_section_states_what_the_ruleset_requires():
    section = _flat(_misbehaves_section())
    ruleset = json.loads(_RULESET.read_text(encoding="utf-8"))
    rules = {rule["type"]: rule.get("parameters", {}) for rule in ruleset["rules"]}
    assert "the ruleset on `main` requires `test` and no approval, so the label alone does not block a merge" in section
    contexts = [check["context"] for check in rules["required_status_checks"]["required_status_checks"]]
    assert contexts == ["test"]
    assert rules["pull_request"]["required_approving_review_count"] == 0
    assert ruleset["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert f'lists `{ruleset["name"]}` with `"enforcement": "{ruleset["enforcement"]}"`' in section
    command = "gh api repos/Danathar/goodreads-mcp/branches/main --jq .protected"
    assert f"`{command}` prints `true`" in section
    assert command in (_ROOT / "docs" / "branch-protection.md").read_text(encoding="utf-8")


def test_the_hold_and_auto_merge_claims_are_maintenance_md_s():
    section = _flat(_misbehaves_section())
    maintenance = _flat((_ROOT / "docs" / "maintenance.md").read_text(encoding="utf-8"))
    assert "At ACMM L6 non-outreach agent PRs auto-merge when checks pass" in section
    assert "Non-outreach agent pull requests carry no level hold and merge once their checks pass" in maintenance
    assert "The `hold` label (applied to outreach PRs, see maintenance.md) is a marker" in section
    assert "outreach pull requests remain held for human review" in maintenance


def test_no_code_here_reads_the_pause_label():
    section = _flat(_misbehaves_section())
    (label,) = set(re.findall(r"`(hive-pause/[a-z-]+)`", section))
    assert f"`gh pr edit <number> --add-label {label}`" in section
    assert "this repository has no code that reads the label" in section
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    readers = [
        name
        for name in tracked
        if not name.endswith(".md")
        and name != Path(__file__).relative_to(_ROOT).as_posix()
        and (_ROOT / name).is_file()
        and "hive-pause" in (_ROOT / name).read_text(encoding="utf-8", errors="replace")
    ]
    assert readers == [], f"{readers} mention the pause label; the section says nothing here reads it"


def test_the_boundary_paths_are_risk_tiers_tier_2():
    section = _flat(_misbehaves_section())
    assert "A PR that touches `.claude/settings.json` or `.claude/hooks/**` is a change to the agent permission boundary" in section
    assert "([risk-tiers.md](risk-tiers.md), Tier 2)" in section
    tiers = (_ROOT / "docs" / "risk-tiers.md").read_text(encoding="utf-8")
    tier_2 = re.search(r"^## Tier 2\b(.*?)(?=^## )", tiers, re.M | re.S).group(1)
    covers = _flat(re.search(r"^Covers:(.*?)\n\n", tier_2, re.M | re.S).group(1))
    assert "`.claude/settings.json`" in covers
    assert "`.claude/hooks/**`" in covers
