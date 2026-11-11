"""`agent-signature.yml` checks an open agent pull request for the audit's signature line.

`agent-audit.yml` reads, once a month, which merged pull requests an agent
wrote, and reports any without a `— hive: agent=... backend=... model=...`
line. By then the body cannot be fixed. `agent-signature.yml` runs the same
test while the pull request is still open.

Pinned here:

- **One definition of a signature.** Both workflows carry the jq function
  `def signature:`. The copies must be identical, so the open-PR check and the
  monthly audit cannot drift apart on what counts.
- **What passes and what fails.** The step is run against bodies the audit
  would and would not accept, including the near misses: a bare `— hive:`
  prefix, a line missing `model=`, a key that only ends in `agent=`.
- **The body is data.** It reaches the script through `PR_BODY`, never through
  `${{ }}` in the script, and shell text in it is not run.
- **Who it runs for.** `pull_request`, never `pull_request_target`, and only
  for the Hive app or an `ai-fix/` branch.

The step is sliced out of the workflow by `tests/_workflow_steps.py` and run
under `bash -e`, the shell GitHub uses.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_CHECK = _WORKFLOWS / "agent-signature.yml"
_AUDIT = _WORKFLOWS / "agent-audit.yml"
_STEP = "Check the pull request body for a signature line"

_SIGNATURE = "— hive: agent=scanner backend=claude model=claude-opus-5-5"

_needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="the step shells out to jq")


def _definitions(path: Path) -> list[list[str]]:
    """Each `def signature:` jq function in *path*, as its lines with indentation dropped."""
    text = path.read_text(encoding="utf-8")
    found = re.findall(r"^[ \t]*def signature:\n.*?\| first;$", text, re.M | re.S)
    return [[line.strip() for line in block.splitlines()] for block in found]


def test_the_check_and_the_audit_share_one_signature_definition():
    audit = _definitions(_AUDIT)
    check = _definitions(_CHECK)
    assert audit, "agent-audit.yml no longer has a `def signature:` function this test can read"
    assert len(check) == 1, "agent-signature.yml should have exactly one `def signature:` function"
    for copy in audit:
        assert copy == check[0], (
            "agent-signature.yml and agent-audit.yml disagree about what a signature line is; "
            "change both copies of `def signature:` together"
        )


def test_it_runs_on_pull_request_never_pull_request_target():
    text = _CHECK.read_text(encoding="utf-8")
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "pull_request_target" in line]
    assert re.search(r"^on:\n  pull_request:\n    types: \[opened, edited, reopened, synchronize\]\n", text, re.M)


def test_it_runs_only_for_the_hive_app_or_an_ai_fix_branch():
    blocks, _ = _workflow_steps.jobs(_CHECK.read_text(encoding="utf-8"))
    assert list(blocks) == ["signature"]
    condition = " ".join(line.strip() for line in blocks["signature"] if line.strip())
    assert "github.event.pull_request.user.login == 'danathar-atomic-hive[bot]'" in condition
    assert "startsWith(github.event.pull_request.head.ref, 'ai-fix/')" in condition


def test_the_body_reaches_the_script_only_through_the_environment():
    step = _workflow_steps.Workflow(_CHECK).step(_STEP)
    assert step.env == {"PR_BODY": "${{ github.event.pull_request.body }}"}
    assert "${{" not in step.run


def _run(tmp_path: Path, body: str | None):
    env = {} if body is None else {"PR_BODY": body}
    script = _workflow_steps.Workflow(_CHECK).body(_STEP)
    return _workflow_steps.run(script, tmp_path, env=env)


@_needs_jq
@pytest.mark.parametrize(
    "body",
    [
        f"**Summary:** a change.\n\n---\n\nCloses #1\n\n{_SIGNATURE}\n",
        f"Summary\r\n\r\n{_SIGNATURE} effort=medium\r\n",
        f"{_SIGNATURE}",
    ],
    ids=["unix-newlines", "crlf-and-extra-key", "only-line"],
)
def test_a_body_with_a_signature_line_passes(tmp_path: Path, body: str):
    result = _run(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr


@_needs_jq
@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "**Summary:** a change.\n\nFiled by quality agent\n",
        "— hive:\n",
        "— hive: agent=scanner backend=claude\n",
        "— hive: subagent=scanner backend=claude model=x\n",
        f"Quoted: {_SIGNATURE}\n",
        f"`{_SIGNATURE}`\n",
    ],
    ids=["unset", "empty", "footer-only", "bare-prefix", "no-model", "key-only-ends-in-agent", "not-at-line-start",
         "in-backticks"],
)
def test_a_body_without_a_signature_line_fails(tmp_path: Path, body: str | None):
    result = _run(tmp_path, body)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error::" in result.stdout


@_needs_jq
def test_shell_text_in_the_body_is_not_run(tmp_path: Path):
    marker = tmp_path / "ran"
    body = f"$(touch {marker})\n`touch {marker}`\n{_SIGNATURE}\n"
    result = _run(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()
