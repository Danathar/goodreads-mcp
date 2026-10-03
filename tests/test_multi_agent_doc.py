"""`docs/multi-agent.md` describes `ai-fix.yml`; a workflow edit can falsify it.

The page says what the workflow does today: it is requested by the
`ai-fix-requested` label or a `/ai-fix` comment, and it runs no agent. The
second claim is the one that rots. The day someone wires in an agent, the page
is wrong, and nothing else reads it. `test_ai_fix_workflow.py` already guards
the workflow's own summary text the same way; this file guards the page.

Both checks compare the page with the workflow. Neither one asks only that a
string is non-empty.
"""

from __future__ import annotations

from pathlib import Path
import re

_ROOT = Path(__file__).resolve().parent.parent
_DOC = (_ROOT / "docs" / "multi-agent.md").read_text(encoding="utf-8")
_WORKFLOW = (_ROOT / ".github" / "workflows" / "ai-fix.yml").read_text(encoding="utf-8")

_NO_AGENT_SENTENCE = "**It does not run an agent.**"


def _workflow_runs_an_agent() -> bool:
    """The same two signs `test_ai_fix_workflow.py` treats as an agent being wired in."""
    uses_agent_action = re.search(r"^\s*(-\s*)?uses:\s*anthropics/", _WORKFLOW, re.M)
    reads_agent_key = "secrets.ANTHROPIC_API_KEY" in _WORKFLOW
    return bool(uses_agent_action or reads_agent_key)


def test_the_page_says_no_agent_runs_exactly_while_the_workflow_runs_none():
    """True both ways: wiring an agent forces an edit of the page, and so does unwiring one."""
    assert (_NO_AGENT_SENTENCE in _DOC) == (not _workflow_runs_an_agent()), (
        "docs/multi-agent.md and .github/workflows/ai-fix.yml disagree about "
        "whether ai-fix.yml runs an agent"
    )


def test_the_triggers_the_page_names_are_the_ones_the_workflow_gates_on():
    """The label name and the comment command are read out of the job's `if:`."""
    condition = re.search(r"^    if: >-\n((?:      .*\n)+)", _WORKFLOW, re.M)
    assert condition, "ai-fix.yml's job-level `if:` moved; update this reader"
    label = re.search(r"github\.event\.label\.name == '([^']+)'", condition.group(1))
    command = re.search(r"startsWith\(github\.event\.comment\.body, '([^']+)'\)", condition.group(1))
    assert label and command
    assert f"`{label.group(1)}`" in _DOC
    assert f"`{command.group(1)}`" in _DOC
