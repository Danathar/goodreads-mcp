"""The three GitHub templates make checkable claims; these check them.

`.github/pull_request_template.md` and the two `.github/ISSUE_TEMPLATE/`
files are read by contributors at the exact moment they are least able to
notice a claim has gone stale — while filing the bug or opening the PR. Until
now nothing read them: `grep -rF` for each full path over `tests/` returned
nothing. (Full paths, not basenames: this repo has both `.github/labeler.yml`
and `.github/workflows/labeler.yml`, so a basename match proves nothing.)

One claim was already wrong when this file was written. `feature_request.md`
named four data surfaces — RSS, autocomplete, `__NEXT_DATA__`, GraphQL —
while `AGENTS.md`, `client.py`'s docstring, the check-live-endpoints skill and
the Cursor rule all name five. The missing one is **Scraped HTML**, the
surface `list_shelves` rides and the one with the weakest guarantees, which is
precisely what a feature request needs to know about. That is the same defect
#81 fixed in `client.py`, in a copy the fix did not reach; `test_client_docstring.py`
pins the docstring to `AGENTS.md` and this file does the same for the template.

Three kinds of check:

* **Claims are read out of their source, not restated.** The surface list
  comes from `AGENTS.md`; the commands come from `ci.yml`'s `run:` bodies; the
  tool names come from the running server's `tools/list`. Rename a surface or
  a tool and these fail rather than quietly agreeing with a copy of the old
  name kept here.
* **Commands match by containment on a flag-free token identity.** The
  checklist says `pytest -q` and CI runs it with four more flags; requiring
  string equality would make every added flag a failure, and requiring nothing
  would let the checklist name a command CI does not run. So the checklist's
  tokens, minus flags and shell variables, must all appear in one `run:` line
  of `ci.yml`, first token first.
* **The checklist is checked for completeness, in both directions.** A new
  blocking `run:` step in `ci.yml` with no checklist line leaves contributors
  ticking a box that no longer covers what CI runs. Setup steps are exempt, by
  name, and an exemption naming a step that no longer exists is itself a
  failure.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import shlex
import subprocess

import pytest

import _workflow_steps

from goodreads_mcp import server

_ROOT = Path(__file__).resolve().parent.parent
_PR_TEMPLATE = _ROOT / ".github" / "pull_request_template.md"
_ISSUE_TEMPLATES = _ROOT / ".github" / "ISSUE_TEMPLATE"
_BUG = _ISSUE_TEMPLATES / "bug_report.md"
_FEATURE = _ISSUE_TEMPLATES / "feature_request.md"
_AGENTS = _ROOT / "AGENTS.md"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"

# `run:` steps of ci.yml that set the runner up rather than check anything a
# contributor could run locally. A contributor does not tick "pip installed".
_SETUP_STEPS = ("Upgrade pip", "Install package and test deps")

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# "- [ ] ..." — GitHub's task syntax. Anything else renders as a bullet and
# cannot be ticked, which is the whole point of the checklist.
_TASK = re.compile(r"^- \[([ xX])\] (.*)$")
_BACKTICKED = re.compile(r"`([^`]+)`")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tracked() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, check=True
    )
    return set(out.stdout.split())


def _frontmatter(path: Path) -> dict[str, str]:
    """The YAML-ish header GitHub reads to build the issue-template chooser."""
    text = _text(path)
    assert text.startswith("---\n"), f"{path.name} does not open with a frontmatter fence"
    _, _, rest = text.partition("---\n")
    body, fence, _ = rest.partition("\n---\n")
    assert fence, f"{path.name}'s frontmatter is never closed"
    fields = {}
    for line in body.splitlines():
        key, sep, value = line.partition(":")
        assert sep, f"{path.name}'s frontmatter line is not a key: {line!r}"
        fields[key.strip()] = value.strip().strip('"')
    return fields


def _checklist_items(text: str) -> list[str]:
    """The `- [ ]` items, each squashed to one line.

    Markdown wraps: an item is everything up to the next item or blank line,
    joined, so a reflowed checklist still matches phrase for phrase.
    """
    items: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        match = _TASK.match(line)
        if match:
            assert match.group(1) == " ", f"checklist item ships pre-ticked: {line!r}"
            if current:
                items.append(" ".join(current))
            current = [match.group(2).strip()]
            continue
        if current is not None and line.strip() and not line.startswith("#"):
            current.append(line.strip())
            continue
        if current:
            items.append(" ".join(current))
        current = None
    if current:
        items.append(" ".join(current))
    return [item for item in items if item]


def _spans(text: str) -> list[str]:
    return _BACKTICKED.findall(text)


def _commands(text: str) -> list[str]:
    """Backticked spans that are commands: more than one whitespace-separated
    token. A single token is a path or an identifier, checked elsewhere."""
    return [span for span in _spans(text) if len(span.split()) > 1]


def _tokens(command: str) -> tuple[str, ...]:
    """A command's identity: its words, minus flags and shell variables.

    Flags drop so that CI tightening `--cov-fail-under` is not a failure;
    `$`-words drop because the runner substitutes them and the checklist
    cannot.
    """
    return tuple(
        token
        for token in shlex.split(command)
        if not token.startswith("-") and "$" not in token
    )


def _run_lines(body: str) -> list[tuple[str, ...]]:
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = _tokens(stripped)
        except ValueError:  # unbalanced quotes across a continuation
            continue
        if tokens:
            lines.append(tokens)
    return lines


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    return bool(needle) and haystack[:1] == needle[:1] and set(needle) <= set(haystack)


@pytest.fixture(scope="module")
def ci() -> _workflow_steps.Workflow:
    return _workflow_steps.Workflow(_CI)


@pytest.fixture(scope="module")
def tools() -> dict[str, object]:
    return {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}


def _agents_surfaces() -> list[str]:
    section = _text(_AGENTS).split("## The data surfaces", 1)[1].split("\n## ", 1)[0]
    numbered = re.findall(r"^(\d+)\. \*\*(.+?)\*\*", section, re.M)
    assert [n for n, _ in numbered] == [str(i) for i in range(1, len(numbered) + 1)], (
        "AGENTS.md's surface list is no longer numbered 1..N"
    )
    return [name for _, name in numbered]


def _template_surfaces() -> list[str]:
    section = _text(_FEATURE).split("**Where would it come from?**", 1)[1]
    section = section.split("**Read-only?**", 1)[0]
    return re.findall(r"^- \*\*(.+?)\*\*", section, re.M)


# --------------------------------------------------------------------------
# The files exist where GitHub looks for them
# --------------------------------------------------------------------------


def test_all_three_templates_are_tracked_at_the_paths_github_reads():
    """GitHub finds these by path alone; a rename makes them ordinary prose."""
    tracked = _tracked()
    for path in (_PR_TEMPLATE, _BUG, _FEATURE):
        rel = path.relative_to(_ROOT).as_posix()
        assert rel in tracked, f"{rel} is not tracked, so GitHub will not offer it"


def test_both_issue_templates_carry_the_frontmatter_the_chooser_needs():
    """Without `name` and `about` the template is unlabelled in the picker."""
    for path in (_BUG, _FEATURE):
        fields = _frontmatter(path)
        for key in ("name", "about", "labels"):
            assert fields.get(key), f"{path.name}'s frontmatter has no {key}"
        assert fields["labels"] == fields["labels"].lower(), (
            f"{path.name} asks for label {fields['labels']!r}; repo labels are lowercase"
        )


def test_the_pr_template_keeps_its_two_sections():
    text = _text(_PR_TEMPLATE)
    for heading in ("## What", "## Checks"):
        assert heading in text, f"the PR template no longer has a {heading!r} section"


def test_every_pr_checklist_line_uses_githubs_task_syntax():
    """A line that is not `- [ ]` renders as a bullet and cannot be ticked."""
    checks = _text(_PR_TEMPLATE).split("## Checks", 1)[1]
    bullets = [line for line in checks.splitlines() if line.startswith("- ")]
    assert bullets, "the Checks section has no bullets at all"
    for line in bullets:
        assert _TASK.match(line), f"not a task-list item: {line!r}"


# --------------------------------------------------------------------------
# The checklist and ci.yml describe the same checks
# --------------------------------------------------------------------------


def test_every_command_the_checklist_quotes_is_a_command_ci_runs(ci):
    """Otherwise the checklist asks for a check that no longer gates anything."""
    bodies = [_run_lines(step.run) for step in ci.steps if step.run]
    for item in _checklist_items(_text(_PR_TEMPLATE)):
        for command in _commands(item):
            tokens = _tokens(command)
            assert any(
                _contains(line, tokens) for body in bodies for line in body
            ), f"the checklist quotes `{command}`, which no run: step of ci.yml runs"


def test_every_gate_ci_runs_has_a_checklist_line(ci):
    """The other direction: a new blocking step with no box leaves contributors
    ticking a checklist that no longer covers what CI runs."""
    quoted = [
        _tokens(command)
        for item in _checklist_items(_text(_PR_TEMPLATE))
        for command in _commands(item)
    ]
    for step in ci.steps:
        if not step.run or step.name in _SETUP_STEPS:
            continue
        lines = _run_lines(step.run)
        assert any(_contains(line, tokens) for line in lines for tokens in quoted), (
            f"ci.yml step {step.name!r} is a gate with no line in the PR checklist. "
            f"Add one, or name the step in _SETUP_STEPS with a reason."
        )


def test_the_setup_exemptions_still_name_steps_that_exist(ci):
    """An exemption for a deleted step silently widens as the workflow moves."""
    names = ci.run_step_names()
    for exempt in _SETUP_STEPS:
        assert exempt in names, (
            f"_SETUP_STEPS exempts {exempt!r}, which is no longer a run: step of ci.yml"
        )


def test_the_checklists_pytest_line_points_at_the_coverage_gate(ci):
    """`pytest -q` passing locally is only meaningful if it is the CI gate."""
    body = _run_lines(ci.step("Run tests with coverage gate").run)
    assert any(_contains(line, ("pytest",)) for line in body)
    assert "--cov-fail-under" in ci.step("Run tests with coverage gate").run, (
        "the gate step no longer fails under a coverage floor"
    )


def test_the_mcpb_command_is_the_one_ci_runs_verbatim(ci):
    """This one the contributor copies and pastes, so the exact string matters."""
    command = next(
        c for item in _checklist_items(_text(_PR_TEMPLATE)) for c in _commands(item)
        if "mcpb" in c
    )
    assert command in ci.step("Validate MCPB manifest").run, (
        f"the checklist says `{command}`, which is not what the Validate MCPB "
        f"manifest step runs"
    )


def test_ci_is_the_workflow_that_runs_on_a_pull_request():
    """'passes locally' is advice about a PR gate; make sure ci.yml is one."""
    on_block = _text(_CI).split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "pull_request:" in on_block, "ci.yml no longer runs on pull_request"


# --------------------------------------------------------------------------
# The version-sync claim
# --------------------------------------------------------------------------


def test_the_two_versions_the_checklist_pairs_agree_today():
    """The checklist asks for them to be bumped together; they start together."""
    manifest = json.loads(_text(_ROOT / "manifest.json"))["version"]
    match = re.search(r'^version = "(.+?)"', _text(_ROOT / "pyproject.toml"), re.M)
    assert match, "pyproject.toml no longer declares a version the checklist can pair"
    assert manifest == match.group(1), (
        f"manifest.json is {manifest} and pyproject.toml is {match.group(1)}; "
        f"release.yml fails the release when these differ"
    )


def test_release_yml_still_enforces_the_pairing_the_checklist_defers_to():
    """The checklist says "(if publishing a release)" — that gate must exist."""
    body = _workflow_steps.Workflow(_RELEASE, job="release").step("Read version from manifest.json").run
    assert "manifest.json" in body and "pyproject.toml" in body
    assert "exit 1" in body, "the version-sync step no longer fails the release"


def test_every_path_the_templates_name_exists():
    """A backticked filename is a pointer; a dead one sends a reader nowhere."""
    tracked = _tracked()
    for path in (_PR_TEMPLATE, _BUG, _FEATURE):
        for span in _spans(_text(path)):
            if len(span.split()) > 1 or "." not in span or span.startswith("__"):
                continue
            assert span.lstrip("./") in tracked, (
                f"{path.name} points at `{span}`, which is not a tracked file"
            )


# --------------------------------------------------------------------------
# The feature template's surface list, pinned to AGENTS.md
# --------------------------------------------------------------------------


def test_the_feature_template_names_the_surfaces_agents_md_lists():
    """#81 in a copy the fix did not reach: the template said four and dropped
    the scraped-HTML surface, which is the one a requester most needs told
    about because it is the one with no structured equivalent."""
    assert _template_surfaces() == _agents_surfaces(), (
        "the feature template's surface list has drifted from AGENTS.md's "
        f"(template: {_template_surfaces()}, AGENTS.md: {_agents_surfaces()})"
    )


def test_the_feature_templates_counted_word_matches_its_own_list():
    """"four unofficial read surfaces" over a list of five is how #81 read."""
    # Squashed: markdown wraps, and the sentence must match either way.
    text = re.sub(r"\s+", " ", _text(_FEATURE))
    match = re.search(r"rides on (\w+) unofficial read surfaces", text)
    assert match, "the feature template no longer says how many surfaces there are"
    claimed = _NUMBER_WORDS[match.group(1)]
    assert claimed == len(_template_surfaces()), (
        f"the template says {match.group(1)} surfaces and then lists "
        f"{len(_template_surfaces())}"
    )


def test_the_scraped_html_surface_is_one_of_them():
    """Named explicitly: this is the entry that went missing, and a list that
    silently shortens to four again should fail on the specific regression."""
    assert any("scraped html" in name.lower() for name in _template_surfaces()), (
        "the scraped-HTML surface is missing from the feature template again (#81)"
    )


def test_the_template_sends_the_reader_to_the_fuller_list():
    assert "AGENTS.md" in _text(_FEATURE), (
        "the template no longer points at AGENTS.md, the copy it is pinned to"
    )


# --------------------------------------------------------------------------
# The read-only promise and the tool names
# --------------------------------------------------------------------------


def test_the_read_only_claim_is_true_of_every_registered_tool(tools):
    """The template turns feature requests away for being writes; that is only
    fair if nothing already registered is one."""
    assert tools, "the server registers no tools"
    for name, tool in tools.items():
        annotations = tool.annotations
        assert annotations is not None, f"{name} carries no annotations"
        assert annotations.readOnlyHint is True, f"{name} is not marked read-only"
        assert annotations.destructiveHint is False, f"{name} is marked destructive"


def test_the_feature_template_states_the_read_only_scope():
    text = _text(_FEATURE).lower()
    assert "read-only" in text and "no writes" in text, (
        "the feature template no longer states the read-only scope, so requests "
        "for writes have nothing to be turned away by"
    )


def test_the_client_makes_the_same_read_only_promise():
    """Two copies of one claim; the template is the one contributors read."""
    from goodreads_mcp import client

    assert "no writes" in (client.__doc__ or "").lower(), (
        "client.py's docstring no longer promises no writes, so the feature "
        "template is now the only copy of that claim"
    )


def test_every_tool_the_issue_templates_name_is_registered(tools):
    """`get_book` and `get_reviews` are cited as examples; a renamed tool leaves
    the example pointing at nothing."""
    cited = set()
    for path in (_BUG, _FEATURE):
        for span in _spans(_text(path)):
            if re.fullmatch(r"[a-z][a-z0-9_]*", span):
                cited.add(span)
    assert cited, "the issue templates cite no tool by name any more"
    for name in sorted(cited):
        assert name in tools, (
            f"the issue templates cite `{name}`, which the server does not register "
            f"(registered: {sorted(tools)})"
        )
