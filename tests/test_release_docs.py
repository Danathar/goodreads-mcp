"""CONTRIBUTING.md's Releases section, and every other page that says who changes the version.

#165 replaced the push-triggered release with a monthly CalVer one, and moved
every version change into a pull request that the workflow's `prepare` run
opens. The rubric and the risk tiers were updated and are pinned by
`test_review_docs.py`. Two copies were not:

* `.github/pull_request_template.md` still asked every contributor to tick
  "`pyproject.toml` / `manifest.json` versions bumped together (if publishing
  a release)", which is the old procedure and the opposite of CONTRIBUTING's
  new "Don't change the version in a feature pull request". It is the one
  copy a contributor reads at the moment they open a pull request.
* CONTRIBUTING.md's own list of when a release "is refused" named six of the
  seven refusals in the `release` job. It left out the one a person running
  the workflow by hand is most likely to hit: typing a `version` that is not
  the one `pyproject.toml` carries.

Nothing read the Releases section; the only test that opens CONTRIBUTING.md
checks the agent permission rule. So each claim here is joined to the step of
`release.yml` that makes it true:

* **The refusal list is compared to the `::error::` lines, both ways.** Every
  `::error::` the `release` job can print maps to a clause of the sentence, or
  to an exemption naming the `docs/quality.md` row that documents it instead;
  every clause maps to an error; every map entry and exemption still matches
  something. A new refusal with no clause fails, and so does a clause for a
  refusal someone deleted.
* **Every page that talks about changing the version names `prepare`**, or is
  an exemption stating the rule negatively. The sweep covers every tracked
  Markdown and editor-rule file, so a copy nobody remembers is still found.
* **The section's other specifics come out of the workflow:** the schedule
  (from the cron), the inputs (both directions), the opt-in variable, the path
  that decides whether a month has anything to release, the branch the
  `prepare` run pushes and the two files it commits. The example versions in
  CONTRIBUTING.md and README.md must satisfy the workflow's own
  `CALVER_PATTERN`.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_CONTRIBUTING = _ROOT / "CONTRIBUTING.md"
_README = _ROOT / "README.md"
_QUALITY = _ROOT / "docs" / "quality.md"
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_RELEASE_TEXT = _RELEASE.read_text(encoding="utf-8")


def _squash(text: str) -> str:
    """Markdown wraps; a phrase can break across lines."""
    return re.sub(r"\s+", " ", text).strip()


def _releases_section() -> str:
    text = _CONTRIBUTING.read_text(encoding="utf-8")
    match = re.search(r"^## Releases\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, "CONTRIBUTING.md has no `## Releases` section"
    return _squash(match.group(1))


def _release_job() -> _workflow_steps.Workflow:
    return _workflow_steps.Workflow(_RELEASE, job="release")


def _prepare_job() -> _workflow_steps.Workflow:
    return _workflow_steps.Workflow(_RELEASE, job="prepare")


def _calver() -> re.Pattern[str]:
    match = re.search(r"^  CALVER_PATTERN: '(.+)'$", _RELEASE_TEXT, re.M)
    assert match, "release.yml no longer defines CALVER_PATTERN at workflow level"
    return re.compile(match.group(1))


# --------------------------------------------------------------------------
# When a release is refused
# --------------------------------------------------------------------------

# Each `::error::` message the release job prints, by a regex over its text,
# and the clause of CONTRIBUTING.md's "It is refused when ..." sentence that
# tells a reader about it.
_REFUSALS = {
    r"checks on .* are not green": "the commit's checks are not green",
    r"manifest\.json version .* != pyproject\.toml version": "the two files disagree",
    r"is not CalVer": "the version is not CalVer",
    r"asked for .*, but pyproject\.toml says": (
        "a `version` given by hand is not the one `pyproject.toml` carries"
    ),
    r"Tag \$VERSION already exists": "is already tagged",
    r"is not newer than the latest release": "is not newer than the last release",
    r"and prerelease=.* disagree": "a `b1`/`rc1` suffix and the `prerelease` box disagree",
}

# Refusals CONTRIBUTING.md leaves to docs/quality.md's gate table, by the
# regex over the error and the row of that table that states them.
_DOCUMENTED_IN_QUALITY = {
    r"expected exactly one \.mcpb": "bundle carries no compiled module",
    r"declares platforms .* but carries compiled": "bundle carries no compiled module",
}


def _error_lines() -> list[str]:
    """Every `::error::` message a step of the release job can print."""
    lines = []
    for step in _release_job().steps:
        for line in (step.run or "").splitlines():
            if "::error::" in line:
                lines.append(line.split("::error::", 1)[1])
    return lines


def _refusal_clauses() -> list[str]:
    section = _releases_section()
    match = re.search(r"It is refused when (.+?)\. ", section)
    assert match, "CONTRIBUTING.md's Releases section no longer says when a release is refused"
    return [
        clause.strip()
        for clause in re.split(r",\s+(?:or\s+)?(?:when\s+)?|\s+or\s+(?:when\s+)?", match.group(1))
        if clause.strip()
    ]


def test_the_release_job_prints_errors_this_file_can_see():
    """The join below is vacuous if the reader finds nothing."""
    assert len(_error_lines()) >= len(_REFUSALS)


def test_every_refusal_the_release_job_prints_is_documented():
    """A new `::error::` gate with no clause leaves the list short, as it was."""
    known = {**_REFUSALS, **_DOCUMENTED_IN_QUALITY}
    for line in _error_lines():
        hits = [pattern for pattern in known if re.search(pattern, line)]
        assert len(hits) == 1, (
            f"release.yml refuses with {line!r}, which maps to {len(hits)} entries; "
            f"add a clause to CONTRIBUTING.md's refusal sentence and to _REFUSALS"
        )


def test_every_mapped_refusal_still_exists_in_the_release_job():
    """The other direction: an entry for a deleted gate keeps a clause alive."""
    lines = _error_lines()
    for pattern in {**_REFUSALS, **_DOCUMENTED_IN_QUALITY}:
        assert any(re.search(pattern, line) for line in lines), (
            f"no ::error:: in release.yml's release job matches {pattern!r}"
        )


def test_the_refusal_sentence_names_every_refusal():
    clauses = _refusal_clauses()
    for pattern, clause in _REFUSALS.items():
        assert clause in clauses, (
            f"CONTRIBUTING.md does not say a release is refused when {clause} "
            f"(release.yml refuses on /{pattern}/); its clauses are {clauses}"
        )


def test_the_refusal_sentence_names_no_refusal_the_workflow_lacks():
    expected = set(_REFUSALS.values())
    for clause in _refusal_clauses():
        assert clause in expected, (
            f"CONTRIBUTING.md says a release is refused when {clause!r}, "
            f"which no ::error:: in the release job backs"
        )


def test_refusals_left_to_quality_md_are_in_its_gate_table():
    table = _squash(_QUALITY.read_text(encoding="utf-8"))
    for row in set(_DOCUMENTED_IN_QUALITY.values()):
        assert f"| {row}" in table, f"docs/quality.md has no gate row starting {row!r}"


# --------------------------------------------------------------------------
# The rest of the Releases section
# --------------------------------------------------------------------------


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def test_the_schedule_is_the_cron():
    crons = re.findall(r'^\s*- cron: "(.+)"$', _RELEASE_TEXT, re.M)
    assert len(crons) == 1, f"release.yml has {len(crons)} cron lines"
    minute, hour, day, month, weekday = crons[0].split()
    assert (month, weekday) == ("*", "*"), f"the cron {crons[0]!r} is no longer monthly"
    stated = f"{int(hour):02d}:{int(minute):02d} UTC on the {_ordinal(int(day))} of each month"
    assert stated in _releases_section(), (
        f"release.yml runs at {crons[0]!r}; CONTRIBUTING.md should say {stated!r}"
    )


# Lower-case backticked words in the section that are not inputs.
_NOT_INPUTS = {"v", "true"}


def _dispatch_inputs() -> set[str]:
    block = _RELEASE_TEXT.split("  workflow_dispatch:\n", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"^      ([a-z_]+):$", block, re.M))


def test_the_section_names_every_input_and_only_inputs():
    """Both directions: a new input is described, a renamed one is not left behind."""
    words = set(re.findall(r"`([a-z_]+)`", _releases_section()))
    inputs = _dispatch_inputs()
    assert inputs, "release.yml has no workflow_dispatch inputs"
    assert _NOT_INPUTS <= words, f"stale exemptions: {sorted(_NOT_INPUTS - words)}"
    assert words - _NOT_INPUTS == inputs, (
        f"CONTRIBUTING.md names {sorted(words - _NOT_INPUTS)}; "
        f"release.yml's inputs are {sorted(inputs)}"
    )


def test_the_opt_in_variable_is_the_one_the_schedule_reads():
    names = re.findall(r"`([A-Z][A-Z0-9_]+)`", _releases_section())
    assert len(names) == 1, f"expected one repository variable, found {names}"
    (name,) = names
    opted_in = _release_job().step("Check the schedule is enabled").env["OPTED_IN"]
    assert opted_in == f"${{{{ vars.{name} == 'true' }}}}", (
        f"CONTRIBUTING.md says `{name}`; the schedule gate reads {opted_in}"
    )
    assert f"`{name}` is `true`" in _releases_section()


def test_the_path_that_decides_a_release_is_the_one_diffed():
    section = _releases_section()
    match = re.search(r"skipped when nothing under `([^`]+)/` changed", section)
    assert match, "CONTRIBUTING.md no longer says which path decides whether to release"
    body = _release_job().step("Decide whether to release").run
    diffs = re.findall(r'git diff --name-only "\$last"\.\.HEAD -- (\S+)', body)
    assert diffs == [match.group(1)], (
        f"CONTRIBUTING.md says `{match.group(1)}/`; the release job diffs {diffs}"
    )


def test_the_prepare_run_pushes_the_branch_and_files_the_section_names():
    section = _releases_section()
    item = re.search(r"1\. \*\*Propose a version\.\*\*(.+?)(?= 2\. )", section)
    assert item, "CONTRIBUTING.md's first release step is gone"
    body = _prepare_job().step("Open the pull request").run

    branch = re.search(r"pushes `release/<version>`", item.group(1))
    assert branch, "the first release step no longer names the branch it pushes"
    assert 'branch="release/$VERSION"' in body

    named = set(re.findall(r"`([\w.]+\.(?:toml|json))`", item.group(1)))
    (add,) = re.findall(r"^\s*git add (.+)$", body, re.M)
    assert named == set(add.split()), (
        f"CONTRIBUTING.md says the version is set in {sorted(named)}; "
        f"the prepare run commits {add.split()}"
    )


@pytest.mark.parametrize("path", [_CONTRIBUTING, _README], ids=lambda p: p.name)
def test_every_example_version_is_calver_by_the_workflows_own_pattern(path):
    pattern = _calver()
    text = path.read_text(encoding="utf-8")
    examples = re.findall(r"`(v?20\d\d\.\d[^`]*)`", text)  # not the `2026.x` placeholder
    assert examples, f"{path.name} gives no example version"
    for example in examples:
        assert pattern.fullmatch(example), f"{path.name}'s example `{example}` is not CalVer"
    if path is _CONTRIBUTING:
        for suffix in re.findall(r"`((?:b|rc)\d+)`", text):
            assert pattern.fullmatch(examples[0] + suffix), f"`{suffix}` is not a valid suffix"


def test_the_pattern_is_the_one_both_jobs_apply():
    for job in (_prepare_job(), _release_job()):
        uses = [s.name for s in job.steps if "$CALVER_PATTERN" in (s.run or "")]
        assert uses, f"the {job.job} job never applies CALVER_PATTERN"


# --------------------------------------------------------------------------
# Who changes the version, everywhere it is said
# --------------------------------------------------------------------------

_CHANGES_THE_VERSION = re.compile(
    r"\bversions?\b.{0,80}\b(?:bump|bumps|bumped|change|changes|changed|set|sets|applied)\b"
    r"|\b(?:bump|bumps|bumped|change|changes|changed|set|sets)\b.{0,40}\bversions?\b",
    re.I,
)

# Paragraphs that talk about changing the version without naming `prepare`,
# because they state the rule from the other side. File, and a phrase from the
# paragraph.
_STATES_IT_NEGATIVELY = {
    "CONTRIBUTING.md": "Don't change the version in a feature pull request",
    "docs/branch-protection.md": "A direct push that bumps the version is released at the next run",
}


def _paragraphs() -> list[tuple[str, str]]:
    files = subprocess.run(
        ["git", "ls-files", "*.md", "*.mdc"],
        cwd=_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert ".github/pull_request_template.md" in files
    found = []
    for rel in files:
        text = (_ROOT / rel).read_text(encoding="utf-8")
        for chunk in re.split(r"\n\s*\n|\n(?=\s*(?:[-*]|\d+\.) )", text):
            paragraph = _squash(chunk)
            if _CHANGES_THE_VERSION.search(paragraph):
                found.append((rel, paragraph))
    return found


def test_every_page_that_says_who_changes_the_version_names_prepare():
    """The PR template said "bumped together (if publishing a release)"."""
    paragraphs = _paragraphs()
    assert len(paragraphs) >= 4, "the sweep matched almost nothing; check the regex"
    for rel, paragraph in paragraphs:
        if "`prepare`" in paragraph:
            continue
        exempt = _STATES_IT_NEGATIVELY.get(rel)
        assert exempt and exempt in paragraph, (
            f"{rel} talks about changing the version without naming the `prepare` "
            f"run that is the only place it changes: {paragraph[:160]!r}"
        )


def test_the_negative_statements_are_still_there():
    """An exemption whose paragraph was reworded exempts nothing."""
    paragraphs = _paragraphs()
    for rel, phrase in _STATES_IT_NEGATIVELY.items():
        assert any(r == rel and phrase in p for r, p in paragraphs), (
            f"{rel} no longer says {phrase!r}; drop or update the exemption"
        )


def test_the_pr_template_tells_a_contributor_to_leave_the_version_alone():
    text = (_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    items = [_squash(line) for line in text.splitlines() if "`pyproject.toml`" in line]
    assert len(items) == 1, f"expected one checklist line about the version, found {items}"
    (item,) = items
    assert item.startswith("- [ ] "), item
    assert "left unchanged" in item and "`prepare`" in item, (
        f"the PR checklist no longer asks for the version to be left to `prepare`: {item!r}"
    )
    assert "bumped" not in item, item
