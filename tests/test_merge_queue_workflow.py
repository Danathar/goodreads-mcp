"""`merge-queue.yml` has to gate the queue on exactly what pull requests gate on.

A GitHub merge queue tests a pull request's merged result before it lands.
Today `.github/rulesets/main.json` sets `strict_required_status_checks_policy:
true` and Hive's serialized merge lane covers that instead, but the queue
would only send
the `merge_group` event, and a queue whose commit never gets the required
`test` check blocks every merge. `merge-queue.yml` answers that event. Nothing
in CI can fire it until an admin enables the queue, so these tests pin what a
later edit could break without anyone seeing a red run:

* it triggers on `merge_group` and nothing else, so it stays inert on pull
  requests and pushes, where `ci.yml` already runs;
* its one job is called after the check the ruleset requires, read from
  `.github/rulesets/main.json`, so the queue's commit reports that context;
* its steps equal `ci.yml`'s `test` steps (names, `uses`, `with`, `run`,
  `env`), so passing the queue means passing what a pull request passes.

No PyYAML; see `tests/_workflow_steps.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from _workflow_steps import Workflow, jobs

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_RULESET = json.loads((_ROOT / ".github" / "rulesets" / "main.json").read_text(encoding="utf-8"))
_QUEUE = _WORKFLOWS / "merge-queue.yml"


def _required_contexts() -> list[str]:
    (rule,) = [r for r in _RULESET["rules"] if r["type"] == "required_status_checks"]
    return [c["context"] for c in rule["parameters"]["required_status_checks"]]


def _under(text: str, key: str, indent: int) -> list[str]:
    """Keys one level inside `key:` (which sits at `indent`)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line == " " * indent + f"{key}:")
    found = []
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        match = re.match(rf"^{' ' * (indent + 2)}([A-Za-z_-]+):", line)
        if match:
            found.append(match.group(1))
    return found


def test_it_runs_on_merge_group_and_nothing_else():
    text = _QUEUE.read_text(encoding="utf-8")
    assert _under(text, "on", 0) == ["merge_group"]
    assert re.search(r"^    types: \[checks_requested\]\s*$", text, re.M)


def test_its_one_job_is_the_check_the_ruleset_requires():
    text = _QUEUE.read_text(encoding="utf-8")
    assert _under(text, "jobs", 0) == _required_contexts() == ["test"]


def _signature(step) -> tuple:
    return (step.name, step.id, step.if_, step.uses, step.run, step.env, step.with_)


def test_its_steps_are_the_steps_ci_runs_on_a_pull_request():
    queue = Workflow(_QUEUE, job="test").steps
    ci = Workflow(_WORKFLOWS / "ci.yml", job="test").steps
    assert len(ci) > 1
    assert [_signature(s) for s in queue] == [_signature(s) for s in ci]


def test_the_jobs_runner_and_timeout_match_ci():
    # ci.yml has a second job (test-newest-python) that the queue does not
    # copy, so compare against the `test` job only.
    def header(path: Path) -> list[str]:
        blocks, _ = jobs(path.read_text(encoding="utf-8"))
        text = "\n".join(blocks["test"])
        return re.findall(r"^    (runs-on|timeout-minutes): (.+)$", text, re.M)

    assert header(_QUEUE) == header(_WORKFLOWS / "ci.yml")
    assert len(header(_QUEUE)) == 2
