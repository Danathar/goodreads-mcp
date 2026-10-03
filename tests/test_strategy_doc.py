"""docs/strategy.md is a dashboard of reproducible reads; these keep the reads pointed at real things.

The page holds no volatile numbers, so there is little to pin. What can drift is
the names its commands depend on: a `gh run list --workflow X.yml` that names a
renamed workflow prints nothing and looks like "no runs", and a drift-issue
query for a title the workflow no longer files prints nothing and looks like
"no drift". Both fail silently, so each is joined to its source here.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGY = (ROOT / "docs" / "strategy.md").read_text()
NIGHTLY = (ROOT / ".github" / "workflows" / "nightly-compliance.yml").read_text()


def test_every_workflow_the_page_reads_exists():
    named = set(re.findall(r"--workflow ([\w.-]+\.yml)", STRATEGY))
    assert {"nightly-compliance.yml", "ci.yml", "release.yml"} <= named
    missing = sorted(n for n in named if not (ROOT / ".github" / "workflows" / n).is_file())
    assert not missing, f"docs/strategy.md reads workflows that do not exist: {missing}"


def test_drift_issue_title_on_the_page_equals_the_one_the_workflow_files():
    page = re.search(r'select\(\.title == "([^"]+)"', STRATEGY)
    assert page, "docs/strategy.md no longer matches the nightly drift issue by exact title"
    workflow = re.search(r'TITLE: "([^"]+)"', NIGHTLY)
    assert workflow, "nightly-compliance.yml no longer sets a drift issue TITLE"
    assert page.group(1) == workflow.group(1)


def test_schedules_the_page_states_match_the_workflows():
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    for text, source, cron, stated in (
        (NIGHTLY, "nightly-compliance.yml", r'cron: "0 7 \* \* \*"', "07:00 UTC"),
        (release, "release.yml", r'cron: "0 9 1 \* \*"', "09:00 UTC on the 1st"),
    ):
        assert re.search(cron, text), f"{source} no longer runs at {stated}"
        assert stated in STRATEGY, f"docs/strategy.md no longer states {stated}"
