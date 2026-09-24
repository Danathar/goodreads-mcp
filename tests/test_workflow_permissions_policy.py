"""Every workflow's token permissions have to match `.github/policies/workflow-permissions.json`.

A workflow's `permissions:` block decides what its `GITHUB_TOKEN` can do: push
to a branch, open a pull request, comment on an issue. Today the only record of
what each workflow is *supposed* to hold is the workflow itself, so widening a
token is a one-line change inside a file a reviewer may skim for the step that
changed instead.

The policy file is the second record. This module holds the two against each
other in both directions:

* every workflow is listed in the policy, and every policy entry is a
  workflow that exists;
* each workflow's top-level block is exactly what the policy says, and a
  workflow the policy marks `null` declares none;
* the jobs that declare their own block are exactly the jobs the policy lists,
  each with exactly the permissions listed.

So a workflow that asks for one more scope fails here until the policy is
edited too, and that second edit is the one a reviewer cannot miss.
`docs/risk-tiers.md` puts `.github/policies/**` in Tier 2 beside
`.github/workflows/**`, for the same reason.

No PyYAML: the test extra is `pytest` + `pytest-cov`, so the blocks are sliced
out by indentation, the way `tests/test_workflow_timeouts.py` slices out jobs.
The parser is checked against inline and block forms below, so a block it
cannot read fails as a mismatch rather than passing as "no permissions".
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_POLICY_PATH = _ROOT / ".github" / "policies" / "workflow-permissions.json"
_POLICY = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))["workflows"]

# The scopes GitHub accepts in a permissions block, and the levels each takes.
# A misspelt scope is silently ignored by Actions, so it is refused here.
_SCOPES = {
    "actions",
    "attestations",
    "checks",
    "contents",
    "deployments",
    "discussions",
    "id-token",
    "issues",
    "models",
    "packages",
    "pages",
    "pull-requests",
    "repository-projects",
    "security-events",
    "statuses",
}
_LEVELS = {"read", "write", "none"}

_KEY = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z0-9_-]+):\s*(?P<value>[^#]*?)\s*(?:#.*)?$")


def _block(lines: list[str], indent: int) -> dict[str, str] | str | None:
    """The `permissions:` value written at *indent* spaces in *lines*.

    Returns the mapping for a block, the raw string for an inline value
    (`read-all`, `{}`), or None when no key sits at that indent. Only the
    first such key counts: at indent 0 that is the workflow's, and callers
    pass one job's lines at a time for indent 4.
    """
    for i, line in enumerate(lines):
        match = _KEY.match(line)
        if not match or len(match["indent"]) != indent or match["key"] != "permissions":
            continue
        if match["value"]:
            return "{}" if match["value"] == "{}" else match["value"]
        block: dict[str, str] = {}
        for entry in lines[i + 1 :]:
            if not entry.strip() or entry.lstrip().startswith("#"):
                continue
            inner = _KEY.match(entry)
            if not inner or len(inner["indent"]) <= indent:
                break
            block[inner["key"]] = inner["value"]
        return block
    return None


def _job_lines(lines: list[str]) -> dict[str, list[str]]:
    """Each job under the top-level `jobs:` key, as the lines of its block."""
    starts = [i for i, line in enumerate(lines) if line.rstrip() == "jobs:"]
    assert len(starts) == 1, "expected exactly one top-level jobs: key"
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[starts[0] + 1 :]:
        if line.strip() and not line.startswith(" "):
            break
        match = _KEY.match(line)
        if match and len(match["indent"]) == 2 and not match["value"]:
            current = match["key"]
            jobs[current] = []
            continue
        if current is not None:
            jobs[current].append(line)
    return jobs


def _declared(text: str) -> dict[str, object]:
    """What a workflow declares: its top-level block and each job's own block."""
    lines = text.splitlines()
    jobs = {}
    for job, body in _job_lines(lines).items():
        block = _block(body, 4)
        if block is not None:
            jobs[job] = block
    return {"workflow": _block(lines, 0), "jobs": jobs}


def _workflow_files() -> list[Path]:
    paths = sorted(p for p in _WORKFLOWS.iterdir() if p.suffix in {".yml", ".yaml"})
    assert paths, f"no workflow files under {_WORKFLOWS}"
    return paths


def test_every_workflow_is_in_the_policy_and_every_entry_is_a_workflow() -> None:
    present = {p.name for p in _workflow_files()}
    assert present == set(_POLICY), (
        f"not in the policy: {sorted(present - set(_POLICY))}; "
        f"in the policy but no such workflow: {sorted(set(_POLICY) - present)}"
    )


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_the_workflow_declares_exactly_what_the_policy_allows(path: Path) -> None:
    declared = _declared(path.read_text(encoding="utf-8"))
    expected = _POLICY[path.name]
    assert declared["workflow"] == expected["workflow"], (
        f"{path.name}'s top-level permissions are {declared['workflow']}; "
        f"{_POLICY_PATH.name} allows {expected['workflow']}. Change both, or neither."
    )
    assert declared["jobs"] == expected["jobs"], (
        f"{path.name}'s job-level permissions are {declared['jobs']}; "
        f"{_POLICY_PATH.name} allows {expected['jobs']}. Change both, or neither."
    )


def test_the_policy_names_only_real_scopes_and_levels() -> None:
    for name, entry in _POLICY.items():
        blocks = [entry["workflow"], *entry["jobs"].values()]
        for block in blocks:
            if block is None:
                continue
            for scope, level in block.items():
                assert scope in _SCOPES, f"{name}: {scope!r} is not a GitHub token scope"
                assert level in _LEVELS, f"{name}: {scope} has level {level!r}"


def test_the_policy_is_not_vacuous() -> None:
    # Every workflow here declares a top-level block today. If the parser
    # stopped seeing them, both sides could read as "nothing" and agree.
    assert all(entry["workflow"] for entry in _POLICY.values())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "permissions:\n  contents: read\n  issues: write  # comment\n\njobs:\n  a:\n    runs-on: x\n",
            {"workflow": {"contents": "read", "issues": "write"}, "jobs": {}},
        ),
        (
            "permissions: read-all\njobs:\n  a:\n    permissions:\n      contents: write\n    steps: []\n",
            {"workflow": "read-all", "jobs": {"a": {"contents": "write"}}},
        ),
        (
            "on: push\njobs:\n  a:\n    permissions: {}\n  b:\n    runs-on: x\n",
            {"workflow": None, "jobs": {"a": "{}"}},
        ),
        (
            "jobs:\n  a:\n    steps:\n      - with:\n          permissions: write\n",
            {"workflow": None, "jobs": {}},
        ),
    ],
    ids=["block-with-comment", "inline-top-and-job-block", "empty-job-block", "step-input-is-not-a-block"],
)
def test_the_parser_reads_each_shape(text: str, expected: dict[str, object]) -> None:
    assert _declared(text) == expected


def test_a_widened_workflow_is_caught() -> None:
    # The regression this module exists for: one more scope in the file.
    name = "ci.yml"
    widened = (_WORKFLOWS / name).read_text(encoding="utf-8").replace(
        "permissions:\n  contents: read\n", "permissions:\n  contents: read\n  pull-requests: write\n", 1
    )
    assert _declared(widened)["workflow"] != _POLICY[name]["workflow"]
