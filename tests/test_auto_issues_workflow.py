"""`auto-issues.yml` is what tells anyone that `main` went red; pin what it does.

`release.yml` refuses to release a commit whose `test` check is not green, and
before this workflow nothing announced that main had gone red. The workflow is
one `run:` body that files or updates a single tracking issue when `CI` fails on
a push to main. Three ways it can go wrong without any test noticing, since the
coverage gate measures `goodreads_mcp` only:

- **It is disconnected.** `workflows: [CI]` is the `name:` of `ci.yml` by
  convention only. Rename CI and the trigger never fires again; nothing fails.
  The trigger, the job's `if:` (failure, push) and the branch filter are pinned
  here, and the trigger name is joined to the name `ci.yml` actually declares.
- **It reports into the wrong issue.** The lookup finds the open tracking issue
  by *listing* open issues and matching the exact title and the Actions bot as
  author. `gh issue list --search "<title> in:title"` is a word match against an
  index that lags, so it would comment on an unrelated issue, or on one anyone
  opened with the same title, or open duplicates (#279 is the same defect in the
  nightly). These tests run the step's shell against a `gh` stub whose `issue
  list` really applies the step's `--jq` filter to a fixture, so the filter is
  exercised and not just spelled.
- **A commit message becomes code.** The head commit's message is whatever its
  author wrote. It must reach the shell as an environment variable; a `${{ }}`
  inside `run:` would paste it into the script. The step is run with a message
  full of shell syntax to show it stays text.

The runner and reader are `tests/_workflow_steps.py`, shared with
`test_nightly_compliance_workflow.py`: the body is sliced out by indentation and
handed to `bash --noprofile --norc -e`, the shell GitHub uses when no `shell:`
is set.

`workflow_run` only fires from the default branch, so none of this can be
exercised end to end until the workflow is merged; the tests below are the part
that can be checked before that.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_AUTO = _WORKFLOWS / "auto-issues.yml"
_CI = _WORKFLOWS / "ci.yml"

_STEP = "Open or update the CI-failure issue"
_TITLE = "CI failing on main"
_BOT_LOGINS = ["app/github-actions", "github-actions[bot]", "github-actions"]

_WORKFLOW = _workflow_steps.Workflow(_AUTO)
_write_stub = _workflow_steps.write_stub
_recorder = _workflow_steps.recorder
_argv = _workflow_steps.argv
_run = _workflow_steps.run

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="the stub applies the step's --jq filter with jq")

_GH_STUB = r"""
case "$1 $2" in
  "issue list")
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--jq" ]; then jq -r "$2" "$FIXTURE"; break; fi
      shift
    done
    ;;
esac
"""

_ENV = {
    "GH_TOKEN": "x",
    "GH_REPO": "Danathar/goodreads-mcp",
    "TITLE": _TITLE,
    "HEAD_SHA": "0123456789abcdef0123456789abcdef01234567",
    "RUN_URL": "https://github.com/Danathar/goodreads-mcp/actions/runs/12345",
    "COMMIT_MESSAGE": "fix: a thing\n\nlonger explanation\non two lines",
}


def _issue(number: int, title: str, login: str) -> dict:
    return {"number": number, "title": title, "author": {"login": login}}


def _report(tmp_path: Path, issues: list[dict], **env: str):
    """Run the step with `issues` as the repo's open issues; return (result, gh calls)."""
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    log = tmp_path / "argv"
    fixture = tmp_path / "issues.json"
    fixture.write_text(json.dumps(issues), encoding="utf-8")
    _write_stub(stubs, "gh", _recorder(log) + _GH_STUB)
    _write_stub(stubs, "date", 'echo "2026-01-02"\n')

    body = _WORKFLOW.body(_STEP)
    result = _run(
        body, tmp_path, path_dirs=[stubs], env={**_ENV, "FIXTURE": str(fixture), **env}
    )
    return result, [record[1:] for record in _argv(log)]


def _flag(call: list[str], flag: str) -> str:
    return call[call.index(flag) + 1]


def _text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The lookup
# --------------------------------------------------------------------------


@needs_jq
@pytest.mark.parametrize("login", _BOT_LOGINS)
def test_the_open_issue_the_bot_filed_gets_the_comment(tmp_path: Path, login: str):
    """GraphQL reports an app actor as `app/github-actions`; the REST spellings differ."""
    result, calls = _report(tmp_path, [_issue(42, _TITLE, login)])

    assert result.returncode == 0, result.stderr
    assert [call[:2] for call in calls] == [["issue", "list"], ["issue", "comment"]]
    assert calls[1][2] == "42"


@needs_jq
def test_an_issue_with_the_same_title_by_a_user_is_ignored_and_a_new_one_is_opened(tmp_path: Path):
    """Anyone can open an issue with this title; failures must not be reported into it."""
    result, calls = _report(tmp_path, [_issue(7, _TITLE, "some-user")])

    assert result.returncode == 0, result.stderr
    assert [call[:2] for call in calls] == [["issue", "list"], ["issue", "create"]]
    assert _flag(calls[1], "--title") == _TITLE


@needs_jq
def test_a_title_that_only_shares_words_is_ignored_even_from_the_bot(tmp_path: Path):
    """`in:title` is a word match; this is the neighbour it would have matched."""
    near = [
        _issue(3, "CI failing on main branch", "app/github-actions"),
        _issue(4, "ci failing on main", "app/github-actions"),
        _issue(5, "main is failing CI", "app/github-actions"),
    ]
    result, calls = _report(tmp_path, near)

    assert result.returncode == 0, result.stderr
    assert [call[:2] for call in calls] == [["issue", "list"], ["issue", "create"]]


@needs_jq
def test_the_exact_issue_is_found_among_lookalikes(tmp_path: Path):
    """The bot's exact-title issue wins over the user's copy listed before it."""
    issues = [
        _issue(1, _TITLE, "some-user"),
        _issue(2, "CI failing on main branch", "app/github-actions"),
        _issue(3, _TITLE, "app/github-actions"),
    ]
    _, calls = _report(tmp_path, issues)

    assert [call[:3] for call in calls] == [["issue", "list", "--state"], ["issue", "comment", "3"]]


@needs_jq
def test_no_open_issues_opens_one(tmp_path: Path):
    result, calls = _report(tmp_path, [])

    assert result.returncode == 0, result.stderr
    assert [call[:2] for call in calls] == [["issue", "list"], ["issue", "create"]]


def test_the_lookup_lists_open_issues_and_never_searches(tmp_path: Path):
    """The search index lags, and a missed issue is a duplicate."""
    if shutil.which("jq") is None:
        pytest.skip("the stub applies the step's --jq filter with jq")
    _, calls = _report(tmp_path, [])

    lookup = calls[0]
    assert _flag(lookup, "--state") == "open"
    assert int(_flag(lookup, "--limit")) >= 1000, "a small --limit silently drops the tracking issue"
    assert "--search" not in lookup
    assert "--search" not in (_WORKFLOW.step(_STEP).run or "")


# The report
# --------------------------------------------------------------------------


@needs_jq
def test_the_issue_body_carries_the_date_the_commit_the_run_and_the_first_line(tmp_path: Path):
    _, calls = _report(tmp_path, [])

    body = _flag(calls[1], "--body")
    assert "failed on a push to `main` on 2026-01-02." in body
    assert f"Commit: {_ENV['HEAD_SHA']}" in body
    assert f"Run: {_ENV['RUN_URL']}" in body
    assert "\n    fix: a thing\n" in body
    assert "longer explanation" not in body, "only the first line of the message belongs in the report"


@needs_jq
def test_the_comment_is_the_same_text_as_a_fresh_issue(tmp_path: Path):
    """A follow-up that dropped the run link would be unactionable."""
    _, opened = _report(tmp_path, [])
    other = tmp_path / "second"
    other.mkdir()
    _, commented = _report(other, [_issue(42, _TITLE, "app/github-actions")])

    assert _flag(commented[1], "--body") == _flag(opened[1], "--body")



@needs_jq
def test_a_commit_message_full_of_shell_syntax_stays_text(tmp_path: Path):
    """The message is attacker-influenced. It is data in `$COMMIT_MESSAGE`, never code."""
    marker = tmp_path / "pwned"
    hostile = f'$(touch {marker}) `touch {marker}`; touch {marker} # "\'\nsecond line'
    result, calls = _report(tmp_path, [], COMMIT_MESSAGE=hostile)

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    first_line = hostile.split("\n")[0]
    assert f"\n    {first_line}\n" in _flag(calls[1], "--body")


@needs_jq
def test_a_one_line_commit_message_is_reported_whole(tmp_path: Path):
    _, calls = _report(tmp_path, [], COMMIT_MESSAGE="ci: bump a pin")

    assert "\n    ci: bump a pin\n" in _flag(calls[1], "--body")


def test_no_event_expression_is_pasted_into_a_shell_script():
    """`${{ }}` in `run:` is substituted before the shell parses it."""
    assert not _workflow_steps.EXPR.search(_WORKFLOW.step(_STEP).run or "")
    env = _WORKFLOW.step(_STEP).env
    assert env["COMMIT_MESSAGE"] == "${{ github.event.workflow_run.head_commit.message }}"
    assert env["HEAD_SHA"] == "${{ github.event.workflow_run.head_sha }}"
    assert env["RUN_URL"] == "${{ github.event.workflow_run.html_url }}"
    assert env["GH_REPO"] == "${{ github.repository }}"


# --------------------------------------------------------------------------
# The trigger, and the workflow around the step
# --------------------------------------------------------------------------


def test_the_trigger_names_the_workflow_ci_yml_declares():
    """Rename `name: CI` and this workflow never fires again, with nothing failing."""
    ci_name = re.search(r"^name: (.+)$", _text_of(_CI), re.M).group(1).strip()
    trigger = re.search(r"^on:\n  workflow_run:\n    workflows: \[(.+)\]$", _WORKFLOW.text, re.M)
    assert trigger, "auto-issues.yml no longer triggers on workflow_run"
    assert [name.strip() for name in trigger.group(1).split(",")] == [ci_name]


def test_the_trigger_is_completed_runs_on_main_and_nothing_else():
    on = re.search(r"^on:\n((?:  .*\n)+)", _WORKFLOW.text, re.M)
    assert on
    assert on.group(1).splitlines() == [
        "  workflow_run:",
        "    workflows: [CI]",
        "    types: [completed]",
        "    branches: [main]",
    ]


def test_ci_itself_still_runs_on_pushes_to_main():
    """`event == 'push'` below is only meaningful while CI is triggered by one."""
    assert re.search(r"^  push:\n    branches: \[main\]$", _text_of(_CI), re.M)


def test_only_a_failed_push_run_gets_a_job():
    """`completed` also covers success, cancellation, and pull-request runs."""
    condition = re.search(r"^    if: (.+)$", _WORKFLOW.text, re.M)
    assert condition
    assert condition.group(1).split(" && ") == [
        "github.event.workflow_run.conclusion == 'failure'",
        "github.event.workflow_run.event == 'push'",
    ]


def test_the_workflow_may_write_issues_and_nothing_else():
    """No `contents`: it never checks out, and `gh issue` needs only `issues: write`."""
    permissions = re.search(r"^permissions:\n((?:  .*\n)+)", _WORKFLOW.text, re.M)
    assert permissions
    assert permissions.group(1).splitlines() == ["  issues: write"]


def test_two_failures_queue_rather_than_racing_into_two_issues():
    assert re.search(r"^concurrency:\n  group: \S+\n  cancel-in-progress: false$", _WORKFLOW.text, re.M)


def test_the_workflow_runs_no_repository_code_and_no_third_party_action():
    assert not re.search(r"^\s*(?:- )?uses:", _WORKFLOW.text, re.M)
    assert _WORKFLOW.run_step_names() == {_STEP}
