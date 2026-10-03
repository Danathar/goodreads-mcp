"""`docs/ai-ops-runbook.md` quotes the titles of the issues workflows file; the workflows own them.

The runbook tells the maintainer to look for an open issue with an exact title:
the nightly drift issue `nightly-compliance.yml` files, and the CI-failure issue
`auto-issues.yml` files. Each workflow creates its issue (and finds an existing
one) from its `TITLE` variable. Reword either side and the runbook sends the
maintainer to look for an issue that is never filed, with nothing failing.
This test compares the strings; it does not check that the file exists.
"""

from __future__ import annotations

from pathlib import Path
import re

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
