"""`docs/ai-ops-runbook.md` quotes the nightly drift issue's title; the workflow owns it.

The runbook tells the maintainer to look for an open issue with this exact
title. `nightly-compliance.yml` creates that issue (and finds an existing one)
from its `TITLE` variable. Reword either side and the runbook sends the
maintainer to search for an issue that is never filed, with nothing failing.
This test compares the two strings; it does not check that the file exists.
"""

from __future__ import annotations

from pathlib import Path
import re

_ROOT = Path(__file__).resolve().parent.parent
_NIGHTLY = _ROOT / ".github" / "workflows" / "nightly-compliance.yml"
_RUNBOOK = _ROOT / "docs" / "ai-ops-runbook.md"


def test_runbook_quotes_the_title_the_nightly_workflow_files():
    match = re.search(r'^\s*TITLE: "([^"]+)"\s*$', _NIGHTLY.read_text(encoding="utf-8"), re.M)
    assert match, "nightly-compliance.yml no longer sets TITLE in the form this test reads"
    title = match.group(1)

    runbook = _RUNBOOK.read_text(encoding="utf-8")
    assert f"> Title: `{title}`" in runbook
    assert f'--search "{title} in:title"' in runbook
