"""`.github/rulesets/main.json` has to keep `main` behind a pull request (#148).

`main` had no branch protection and no ruleset, so any token with
`contents: write` could push to it directly, past the hold label, the review
and the coverage gate, and a push to `main` starts `release.yml`. The ruleset
is committed here for an admin to apply; `docs/branch-protection.md` says how.

A pull request cannot check what GitHub enforces, only what the file says.
These tests keep the file saying the right thing:

* it targets the default branch, has no bypass actor, and requires a pull
  request with no deletion and no force-push;
* every check it requires is a job `ci.yml` runs on every pull request to
  `main`. A renamed job, a `name:` override, an `if:` or a path filter would
  each leave pull requests waiting for a check that never reports, and the
  first fix anyone reaches for then is deleting the ruleset;
* the docs that route a change to it still name it.

No PyYAML, for the reason `tests/test_workflow_timeouts.py` gives: `ci.yml`
is read by indentation.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

_ROOT = Path(__file__).resolve().parent.parent
_RULESET = json.loads((_ROOT / ".github" / "rulesets" / "main.json").read_text(encoding="utf-8"))
_CI = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
_GITHUB_ACTIONS_APP_ID = 15368


def _rules() -> dict[str, dict]:
    return {rule["type"]: rule.get("parameters", {}) for rule in _RULESET["rules"]}


def _block(text: str, key: str, indent: int) -> str:
    """The lines under `key:` at `indent`, up to the next key at that indent or less."""
    lines = text.splitlines()
    head = re.compile(rf"^{' ' * indent}{re.escape(key)}:\s*(?:#.*)?$")
    for i, line in enumerate(lines):
        if head.match(line):
            body = []
            for nxt in lines[i + 1 :]:
                stripped = nxt.lstrip(" ")
                if stripped and not stripped.startswith("#") and len(nxt) - len(stripped) <= indent:
                    break
                body.append(nxt)
            return "\n".join(body)
    raise AssertionError(f"ci.yml has no `{key}:` at indent {indent}")


def test_the_ruleset_keeps_main_behind_a_pull_request():
    assert _RULESET["target"] == "branch"
    assert _RULESET["enforcement"] == "active"
    assert _RULESET["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert _RULESET["conditions"]["ref_name"]["exclude"] == []
    # A bypass for Actions or for an App hands back the direct push.
    assert _RULESET["bypass_actors"] == []
    rules = _rules()
    assert {"deletion", "non_fast_forward", "pull_request", "required_status_checks"} <= rules.keys()
    # 0, not 1: nobody can approve their own pull request, and on a
    # single-maintainer repository 1 would stop everything merging.
    assert rules["pull_request"]["required_approving_review_count"] == 0


def test_every_required_check_is_a_job_every_pull_request_gets():
    on = _block(_CI, "on", 0)
    pull_request = _block(on, "pull_request", 2)
    assert re.search(r"^\s+branches:\s*\[\s*main\s*\]\s*$", pull_request, re.M), pull_request
    assert not re.search(r"^\s+(paths|paths-ignore|branches-ignore|types):", pull_request, re.M), (
        "ci.yml's pull_request trigger has a filter, so some pull requests "
        "never get the check the ruleset requires"
    )
    jobs = _block(_CI, "jobs", 0)
    required = _rules()["required_status_checks"]["required_status_checks"]
    assert required, "the ruleset requires no check"
    for check in required:
        assert check["integration_id"] == _GITHUB_ACTIONS_APP_ID, check
        job = _block(jobs, check["context"], 2)
        # A job reports under its `name:` when it has one, not its id.
        assert not re.search(r"^    name:", job, re.M), f"job `{check['context']}` reports under its name:"
        assert not re.search(r"^    if:", job, re.M), f"job `{check['context']}` can be skipped"
        assert "strategy:" not in job, f"a matrix renames job `{check['context']}`'s check"


def test_the_docs_route_a_change_to_the_ruleset():
    doc = (_ROOT / "docs" / "branch-protection.md").read_text(encoding="utf-8")
    assert "(../.github/rulesets/main.json)" in doc
    assert "gh api --method POST repos/Danathar/goodreads-mcp/rulesets" in doc
    for context in (c["context"] for c in _rules()["required_status_checks"]["required_status_checks"]):
        assert f"**One required check, `{context}`.**" in doc, context
    tiers = (_ROOT / "docs" / "risk-tiers.md").read_text(encoding="utf-8")
    tier2 = tiers.split("## Tier 2", 1)[1].split("## Tier 3", 1)[0]
    assert "`.github/rulesets/**`" in tier2
