"""`agent-audit.yml` decides which pull requests an agent wrote, and fails one rule.

It is a `run:` step of `gh` and `jq`, and its selection and its one failing
rule are exactly the parts a later edit can break without any other test
noticing. They were each wrong once in the AIB copy this was modelled on, so
each is pinned here by running the step against a `gh` stub that serves REST-
and `gh pr list`-shaped fixtures:

- **Who counts as an agent.** The Hive app (`app/danathar-atomic-hive`, the
  login `gh pr list` returns) or a signature line naming `agent=`, `backend=`
  and `model=`. Dependabot is also a bot and must not count; a bare `— hive:`
  prefix is not a signature.
- **Pagination.** `gh pr view --json commits,files` stops at 100 without saying
  so, so commits and files come from `gh api --paginate`. The stub serves a
  second page only when `--paginate` is passed, so dropping the flag changes
  the commit count the summary reports.
- **The failing rule.** An agent pull request that touches `.claude/settings.json`
  or `.claude/hooks/**` and was merged by a bot fails (CONTRIBUTING.md, "a human
  reads it and merges it, whoever wrote it"); merged by a human it passes. A
  missing signature or Signed-off-by trailer is reported, never failed: no
  document here makes either a rule.
- **Refusals.** A window that fills the list cap, and a `since` that is not a
  real `YYYY-MM-DD`, stop the run with exit 2 rather than auditing part of it.

The step is sliced out of the workflow by `tests/_workflow_steps.py` and run
under `bash -e`, the shell GitHub uses (the step sets `pipefail` itself).
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW = _workflow_steps.Workflow(_ROOT / ".github" / "workflows" / "agent-audit.yml")
_STEP = "Audit merged agent pull requests"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None, reason="the audit step shells out to jq"
)

_HIVE = "app/danathar-atomic-hive"
_SIGNATURE = "— hive: agent=quality backend=claude model=claude-opus-5-5 effort=medium"
_HUMAN = {"login": "Danathar", "is_bot": False}
_APP = {"login": "app/github-actions", "is_bot": True}

# Serves what the step asks of `gh`. `gh pr list` returns the fixture list;
# `gh api --paginate .../pulls/N/{commits,files}` returns every page file for
# that pull request, as `--jq` would apply per page, and without `--paginate`
# only the first. `gh pr view` is refused: it is the call that truncates.
_GH = r"""
echo "gh $*" >> "$FIXTURES/calls"
case "$1 $2" in
  "pr list") cat "$FIXTURES/prs.json" ;;
  "api --paginate") pages=all ;;
  "pr view") echo "gh pr view truncates commits and files at 100" >&2; exit 9 ;;
  *) pages=first ;;
esac
if [ "$1" = api ]; then
  [ "$2" = --paginate ] && shift
  path="$2"
  kind="${path##*/}"; number="${path%/*}"; number="${number##*/}"
  filter=.
  [ "$3" = --jq ] && filter="$4"
  for page in "$FIXTURES/$number.$kind".*; do
    jq -r "$filter" "$page"
    [ "$pages" = all ] || break
  done
fi
"""


def _pr(number: int, author: dict, *, body: str = "", merged_by: dict | None = None, title: str = "t"):
    return {
        "number": number,
        "title": title or f"pull request {number}",
        "author": author,
        "mergedAt": "2026-09-20T10:00:00Z",
        "mergedBy": merged_by if merged_by is not None else _HUMAN,
        "body": body,
        "url": f"https://github.com/Danathar/goodreads-mcp/pull/{number}",
    }


def _hive(number: int, **kwargs):
    return _pr(number, {"login": _HIVE, "is_bot": True}, body=f"Body\n\n{_SIGNATURE}\n", **kwargs)


def _maintainer(number: int, body: str, **kwargs):
    return _pr(number, {"login": "Danathar", "is_bot": False}, body=body, **kwargs)


def _commit(i: int, *, signed: bool = True):
    message = f"change {i}\n\nwhy\n"
    if signed:
        message += "\nSigned-off-by: quality <quality@hive.kubestellar.io>"
    return {"sha": f"{i:040x}", "commit": {"message": message}}


def _fixtures(tmp_path: Path, prs: list[dict], commits: dict[int, list[list[dict]]] | None = None,
              files: dict[int, list[list[str]]] | None = None) -> Path:
    directory = tmp_path / "fixtures"
    directory.mkdir()
    (directory / "prs.json").write_text(json.dumps(prs), encoding="utf-8")
    for pr in prs:
        number = pr["number"]
        for kind, source, default in (
            ("commits", commits, [[_commit(1)]]),
            ("files", files, [["README.md"]]),
        ):
            pages = (source or {}).get(number, default)
            for index, page in enumerate(pages):
                payload = page if kind == "commits" else [{"filename": name} for name in page]
                (directory / f"{number}.{kind}.{index}").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _audit(tmp_path: Path, prs: list[dict], *, since: str = "2026-09-01", **kwargs):
    fixtures = _fixtures(tmp_path, prs, **kwargs)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _workflow_steps.write_stub(stubs, "gh", _GH)
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    result = _workflow_steps.run(
        _WORKFLOW.body(_STEP),
        tmp_path,
        path_dirs=[stubs],
        env={
            "GH_TOKEN": "x",
            "REPO": "Danathar/goodreads-mcp",
            "SINCE": since,
            "FIXTURES": str(fixtures),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        pipefail=True,
    )
    calls = (fixtures / "calls").read_text(encoding="utf-8") if (fixtures / "calls").exists() else ""
    return result, summary.read_text(encoding="utf-8"), calls


def _rows(summary: str) -> list[str]:
    return [line for line in summary.splitlines() if line.startswith("| [#")]


# --------------------------------------------------------------------------
# Who counts as an agent
# --------------------------------------------------------------------------


def test_dependabot_is_a_bot_but_not_an_agent(tmp_path: Path):
    dependabot = _pr(1, {"login": "app/dependabot", "is_bot": True}, body="Bumps actions/checkout")

    result, summary, calls = _audit(tmp_path, [dependabot])

    assert result.returncode == 0, result.stderr
    assert _rows(summary) == []
    assert "0 of the 1 pull requests" in summary
    assert "pulls/1/" not in calls, "an unselected pull request must not be fetched"


def test_a_bot_opened_pull_request_with_a_valid_signature_is_selected_by_the_signature(tmp_path: Path):
    """Not any bot, but the signature is its own way in: someone else's app that signs as Hive is a row."""
    other = _pr(2, {"login": "app/other", "is_bot": True}, body=f"x\n{_SIGNATURE}\n")

    _, summary, _ = _audit(tmp_path, [other])

    assert len(_rows(summary)) == 1


def test_the_hive_app_pull_request_is_selected_and_reported(tmp_path: Path):
    result, summary, _ = _audit(tmp_path, [_hive(3)])

    assert result.returncode == 0, result.stderr
    (row,) = _rows(summary)
    cells = [cell.strip() for cell in row.strip("|").split(" | ")]
    assert cells[1:] == [
        "2026-09-20",
        _HIVE,
        "Danathar",
        "agent=quality backend=claude model=claude-opus-5-5 effort=medium",
        "1",
        "1 of 1",
        "none",
    ]


def test_the_rest_spelling_of_the_apps_login_is_selected_too(tmp_path: Path):
    app = _pr(4, {"login": "danathar-atomic-hive[bot]", "is_bot": True}, body="")

    _, summary, _ = _audit(tmp_path, [app])

    assert len(_rows(summary)) == 1


def test_a_maintainer_pull_request_with_a_valid_signature_is_selected(tmp_path: Path):
    """An omp-backed run pushes under the maintainer's own login; the signature is all there is."""
    result, summary, _ = _audit(tmp_path, [_maintainer(5, f"Body\n\n{_SIGNATURE}\n")])

    assert result.returncode == 0, result.stderr
    assert len(_rows(summary)) == 1
    assert "Danathar | Danathar | agent=quality" in summary


@pytest.mark.parametrize(
    "line",
    [
        "— hive:",
        "— hive: agent=quality",
        "— hive: agent=quality backend=claude",
        "— hive: backend=claude model=x",
        "— hive: agent= backend=claude model=x",
        "see — hive: agent=quality backend=claude model=x",
    ],
)
def test_a_bare_or_partial_signature_does_not_select_a_maintainer_pull_request(tmp_path: Path, line: str):
    _, summary, _ = _audit(tmp_path, [_maintainer(6, f"Body\n\n{line}\n")])

    assert _rows(summary) == []


def test_a_hive_app_pull_request_with_no_valid_signature_is_reported_and_not_failed(tmp_path: Path):
    """No document makes the signature a rule, so its absence is a row, not a red run."""
    pr = _pr(7, {"login": _HIVE, "is_bot": True}, body="— hive:\n")

    result, summary, _ = _audit(tmp_path, [pr])

    assert result.returncode == 0, result.stderr
    assert "| none |" in _rows(summary)[0]
    assert "1 without a complete signature line" in summary


def test_a_commit_without_signed_off_by_is_reported_and_not_failed(tmp_path: Path):
    result, summary, _ = _audit(
        tmp_path, [_hive(8)], commits={8: [[_commit(1), _commit(2, signed=False)]]}
    )

    assert result.returncode == 0, result.stderr
    assert "| 2 | 1 of 2 |" in _rows(summary)[0]
    assert "1 with a commit lacking Signed-off-by" in summary


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------


def test_commits_and_files_are_read_past_the_first_hundred(tmp_path: Path):
    commits = [_commit(i) for i in range(150)]
    names = [f"docs/f{i}.md" for i in range(120)] + [".claude/hooks/x"]
    result, summary, calls = _audit(
        tmp_path,
        [_hive(9)],
        commits={9: [commits[:100], commits[100:]]},
        files={9: [names[:100], names[100:]]},
    )

    assert result.returncode == 0, result.stderr
    assert "| 150 | 150 of 150 | `.claude/hooks/x` |" in _rows(summary)[0], "the hook on file 121 was missed"
    assert "gh api --paginate repos/Danathar/goodreads-mcp/pulls/9/commits" in calls
    assert "gh api --paginate repos/Danathar/goodreads-mcp/pulls/9/files" in calls
    assert "gh pr view" not in calls


def test_a_pull_request_that_fills_the_rest_commit_cap_is_refused(tmp_path: Path):
    """GET /pulls/{n}/commits holds at most 250; a pull request at the cap is not fully read."""
    commits = [_commit(i) for i in range(250)]

    result, _, _ = _audit(tmp_path, [_hive(10)], commits={10: [commits[:100], commits[100:200], commits[200:]]})

    assert result.returncode == 2
    assert "#10 reached the REST list cap" in result.stdout


# --------------------------------------------------------------------------
# The one rule that fails the run
# --------------------------------------------------------------------------


def test_an_agent_pull_request_touching_the_hooks_merged_by_a_bot_fails(tmp_path: Path):
    pr = _hive(11, merged_by=_APP)

    result, summary, _ = _audit(tmp_path, [pr], files={11: [[".claude/hooks/guard-bash.py"]]})

    assert result.returncode == 1
    assert "#11: touches the agent permission boundary and was merged by app/github-actions" in summary
    assert "`.claude/hooks/guard-bash.py`" in summary


def test_the_same_pull_request_merged_by_a_human_passes(tmp_path: Path):
    pr = _hive(12, merged_by=_HUMAN)

    result, summary, _ = _audit(tmp_path, [pr], files={12: [[".claude/hooks/x"]]})

    assert result.returncode == 0, result.stderr
    assert "`.claude/hooks/x`" in _rows(summary)[0]
    assert "Findings" not in summary


@pytest.mark.parametrize("merged_by", [{"login": "someone[bot]", "is_bot": False}, {"login": "x", "is_bot": True}])
def test_a_bot_is_recognised_by_its_login_or_by_the_flag(tmp_path: Path, merged_by: dict):
    result, _, _ = _audit(
        tmp_path, [_hive(13, merged_by=merged_by)], files={13: [[".claude/settings.json"]]}
    )

    assert result.returncode == 1


def test_an_unknown_merger_is_not_taken_for_a_human(tmp_path: Path):
    pr = _hive(14)
    pr["mergedBy"] = None

    result, summary, _ = _audit(tmp_path, [pr], files={14: [[".claude/settings.json"]]})

    assert result.returncode == 1
    assert "merged by unknown" in summary


def test_a_bot_merge_that_touches_nothing_on_the_boundary_passes(tmp_path: Path):
    result, _, _ = _audit(tmp_path, [_hive(15, merged_by=_APP)], files={15: [["tests/x.py", ".claude/skills/a/SKILL.md"]]})

    assert result.returncode == 0, result.stderr


def test_only_agent_pull_requests_can_trip_the_rule(tmp_path: Path):
    """A maintainer PR with no signature, bot-merged, touching the hooks is not this audit's business."""
    pr = _maintainer(16, "no signature", merged_by=_APP)

    result, _, _ = _audit(tmp_path, [pr], files={16: [[".claude/hooks/x"]]})

    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_window_that_fills_the_list_cap_is_refused(tmp_path: Path):
    prs = [_pr(n, {"login": "Danathar", "is_bot": False}) for n in range(1, 501)]

    result, summary, calls = _audit(tmp_path, prs)

    assert result.returncode == 2
    assert "500 pull requests merged since 2026-09-01 reached the 500 cap" in result.stdout
    assert summary == ""
    assert "gh api" not in calls


@pytest.mark.parametrize("since", ["yesterday", "2026-9-1", "2026-13-45", "2026-09-01T00:00", "2026-02-30"])
def test_a_since_that_is_not_a_real_date_is_refused_before_anything_is_fetched(tmp_path: Path, since: str):
    result, _, calls = _audit(tmp_path, [_hive(17)], since=since)

    assert result.returncode == 2
    assert "since must be" in result.stdout
    assert calls == ""


def test_a_blank_since_defaults_to_the_last_31_days_and_is_passed_to_the_search(tmp_path: Path):
    result, summary, calls = _audit(tmp_path, [], since="")

    assert result.returncode == 0, result.stderr
    assert "--search merged:>=" in calls
    assert "merged since 20" in summary


# --------------------------------------------------------------------------
# The workflow around the step
# --------------------------------------------------------------------------


def test_the_workflow_reads_pull_requests_and_runs_no_action_and_no_expression_in_the_script():
    """No checkout, no third-party code, and no `${{ }}` inside `run:` (inputs go through `env:`)."""
    text = _WORKFLOW.text
    assert "uses:" not in text
    assert "${{" not in (_WORKFLOW.step(_STEP).run or "")
    assert _WORKFLOW.step(_STEP).env["SINCE"] == "${{ inputs.since }}"
    assert "\npermissions:\n  contents: read\n  pull-requests: read\n" in text
    assert "schedule:" in text and "workflow_dispatch:" in text
