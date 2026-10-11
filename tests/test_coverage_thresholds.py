"""The coverage gate is written down in ten places; these keep the copies honest.

`.coverage-thresholds.json` records the gate and a measured snapshot, and until
now **nothing read it** — not CI, not a test, only `.github/labeler.yml` matching
its path. A file nobody reads is a file nobody updates, which is how its note
came to claim ~61% while the suite was actually at 82% (#63).

Two different decay rates, so two different treatments:

- **The gate** (`--cov-fail-under`) is a constant. `.github/workflows/ci.yml`
  enforces it, two JSON files mirror it, and seven prose files quote it (listed
  in `_GATE_PROSE` below). Ten copies of one number is a drift trap with an
  exact answer, so every copy is pinned to the workflow here: change the gate
  in CI and forget a file, and this goes red naming the file you missed. A
  sweep over every tracked text file also catches a NEW copy that quotes the
  flag itself, which is the form most of them take.
- **The measured figure** is a reading of HEAD and moves whenever a test lands.
  It cannot be pinned to reality without running coverage inside the suite
  being measured, which would be both slow and circular. So its copies are
  pinned to EACH OTHER — `.coverage-thresholds.json` is the one to update, and
  `auto-qa-tuning.json`, `docs/metrics.md` and `docs/quality.md` must agree
  with it — and it is checked for coherence: a snapshot below the gate would
  be asserting that CI ought to be failing. The durable part, the command to
  regenerate it, is asserted to be present.

That split is the point. Pinning the unpinnable number to reality would produce
a test that fails on every honest test PR, which is how a guard gets deleted.

The **test counts** in the same table are a third case, and they go the other
way. `docs/quality.md` said 322 offline tests while the suite ran 418 (#85) —
96 low, in the table a reader uses as the evidence for the document's argument
that the offline suite is not this project's weak spot. Unlike coverage, the
count can be read without running the suite: `--collect-only` is a sub-second
subprocess and is not circular, because collecting tests does not execute them.
So it is pinned to reality rather than to a sibling copy. The offline figure is
a floor with a band, not an exact number (#377): an exact count made every PR
that adds tests edit the same line, so concurrent PRs conflicted on it at every
merge. The row now changes only when the suite leaves the band, and the failure
message prints the exact line to write. The `CI, last 30 runs` row has no such
source, so it gets the pinned-to-each-other treatment against
`docs/metrics.md`'s fuller copy.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_THRESHOLDS = _ROOT / ".coverage-thresholds.json"
_TUNING = _ROOT / ".github" / "auto-qa-tuning.json"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_METRICS = _ROOT / "docs" / "metrics.md"

# The opt-in live suite. Everything else is the offline suite CI runs.
_LIVE_DIR = "tests/e2e"

# `--cov-fail-under=N`, `--cov-fail-under N`, or `--cov-fail-under=N.0`.
_CI_GATE = re.compile(r"--cov-fail-under[= ](\d+(?:\.\d+)?)")

# Every prose copy of the gate, as the exact phrase each file uses with the
# number templated. A copy that is not in this table is not pinned, so when you
# quote the gate in a new file, add the phrase here.
_GATE_PROSE = {
    "docs/metrics.md": "| coverage gate | {gate}% (`--cov-fail-under`) |",
    "docs/quality.md": "| coverage floor, {gate}% | `ci.yml` (`--cov-fail-under`) | yes |",
    "docs/review-rubric.md": "Coverage gate ({gate}%) still passes.",
    "AGENTS.md": "CI enforces `--cov-fail-under={gate}` on `goodreads_mcp`.",
    ".github/copilot-instructions.md": "CI runs it with a {gate}% coverage gate",
    "prompts/add-discovery-tool.md": "CI enforces {gate}% coverage.",
    ".claude/skills/add-mcp-tool/SKILL.md": "CI runs `pytest -q --cov-fail-under={gate}`.",
}

# Every prose copy of the measured snapshot, pinned to `.coverage-thresholds.json`.
_SNAPSHOT_PROSE = {
    "docs/metrics.md": "| coverage actual | {measured}% at",
    "docs/quality.md": "| coverage | {measured}% overall",
}

# The test-count row of the same table, pinned to what pytest actually collects:
# the offline figure is a floor ("1,700+"), the live figure is exact.
_COUNT_ROW = "| offline tests | {floor:,}+ passing, {live} skipped (live, opt-in) |"
_COUNT_ROW_RE = re.compile(
    r"\| offline tests \| (?P<floor>\d{1,3}(?:,\d{3})*)\+ passing, "
    r"(?P<live>\d+) skipped \(live, opt-in\) \|"
)
# The offline count may run this far above the floor before the row is stale.
_COUNT_BAND = 1.1
# The floor the failure message suggests: the count rounded down to this.
_COUNT_STEP = 100

# `docs/metrics.md`'s CI row is the fuller copy: successes, non-successes, and
# the window they were read over. `docs/quality.md` quotes the successes alone.
_METRICS_CI_ROW = re.compile(
    r"\|\s*last (?P<window>\d+) `ci\.yml` runs\s*\|(?P<counts>[^|]*)\|"
)
_QUALITY_CI_ROW = re.compile(r"\|\s*CI, last (?P<window>\d+) runs\s*\|\s*(?P<success>\d+) success\s*\|")
_COUNT_AND_LABEL = re.compile(r"(\d+) ([a-z-]+(?: [a-z-]+)*)")

# A `--collect-only -q` line is a node id: `tests/test_x.py::test_y`.
_NODE_ID = re.compile(r"^(tests/[^\s:]+\.py)::")

# The tracked files the `--cov-fail-under` sweep reads. Suffix-scoped so the
# sweep never opens a binary fixture.
_TEXT_SUFFIXES = {".md", ".yml", ".yaml", ".json", ".py", ".toml", ".txt", ".cfg", ".ini"}


@pytest.fixture(scope="module")
def thresholds() -> dict:
    return json.loads(_THRESHOLDS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tuning() -> dict:
    return json.loads(_TUNING.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def collected() -> dict[str, int]:
    """How many tests this repo has, by suite, read from pytest's own collection.

    A separate process so the outer run's options (coverage, `-k`, a single
    file) cannot change the answer; `--collect-only` does not execute anything,
    so this does not recurse. Sub-second: collection is a parse, not a run.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"collecting the suite failed:\n{result.stdout}\n{result.stderr}"
    node_ids = [
        match.group(1)
        for line in result.stdout.replace("\\", "/").splitlines()
        if (match := _NODE_ID.match(line.strip()))
    ]
    assert node_ids, f"collection returned no node ids; output was:\n{result.stdout}"
    live = sum(1 for path in node_ids if path.startswith(f"{_LIVE_DIR}/"))
    return {"offline": len(node_ids) - live, "live": live, "total": len(node_ids)}


def _ci_gate() -> float:
    """The gate CI actually enforces, read from the workflow that enforces it."""
    matches = _CI_GATE.findall(_CI.read_text(encoding="utf-8"))
    assert matches, f"{_CI.name} no longer passes --cov-fail-under; the gate this file pins is gone"
    assert len(set(matches)) == 1, f"{_CI.name} passes conflicting gates: {sorted(set(matches))}"
    return float(matches[0])


def _percent(value: float) -> str:
    """Rendered as an integer when it is one: "55", not "55.0"."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _prose(path: str) -> str:
    """A file's text with whitespace collapsed, so a phrase wrapped mid-sentence still matches."""
    return re.sub(r"\s+", " ", (_ROOT / path).read_text(encoding="utf-8"))


def _tracked_text_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files"], capture_output=True, text=True, check=True
    )
    return [_ROOT / line for line in listing.stdout.splitlines() if Path(line).suffix in _TEXT_SUFFIXES]


def test_the_thresholds_file_is_committed_and_parses(thresholds: dict):
    """A manifest that has stopped being valid JSON records nothing."""
    assert _THRESHOLDS.is_file()
    for key in ("min_line_coverage_percent", "measured_percent", "measured_at", "regenerate", "note"):
        assert key in thresholds, f"{_THRESHOLDS.name} lost its {key!r} field"


def test_the_gate_matches_the_workflow_that_enforces_it(thresholds: dict):
    """The JSON is a mirror; a mirror that disagrees is worse than no mirror."""
    assert thresholds["min_line_coverage_percent"] == _ci_gate(), (
        f"{_THRESHOLDS.name} records a {thresholds['min_line_coverage_percent']}% gate but "
        f"{_CI.name} enforces {_ci_gate()}%"
    )


def test_the_tuning_floor_matches_the_workflow_that_enforces_it(tuning: dict):
    """`auto-qa-tuning.json` is the second mirror, and the one the headroom rule reads."""
    assert tuning["coverage"]["floor_percent"] == _ci_gate(), (
        f"{_TUNING.name} records a {tuning['coverage']['floor_percent']}% floor but "
        f"{_CI.name} enforces {_ci_gate()}%"
    )


@pytest.mark.parametrize("path", sorted(_GATE_PROSE))
def test_every_prose_copy_of_the_gate_matches_the_workflow(path: str):
    """The seven files a human reads the gate from, each pinned to what CI enforces."""
    phrase = _GATE_PROSE[path].format(gate=_percent(_ci_gate()))
    assert phrase in _prose(path), (
        f"{path} no longer says {phrase!r}, which is what {_CI.name} enforces; "
        "update the copy, or the phrase in _GATE_PROSE if the wording changed on purpose"
    )


def test_no_tracked_file_quotes_a_different_gate():
    """The sweep: a new file that quotes `--cov-fail-under=N` is caught without a table entry."""
    gate = _ci_gate()
    wrong = {
        str(path.relative_to(_ROOT)): sorted(set(found))
        for path in _tracked_text_files()
        if (found := [value for value in _CI_GATE.findall(path.read_text(encoding="utf-8")) if float(value) != gate])
    }
    assert not wrong, f"these files quote a --cov-fail-under other than {_CI.name}'s {_percent(gate)}: {wrong}"


def test_the_measured_snapshot_is_at_least_the_gate(thresholds: dict):
    """A snapshot below the gate would be claiming CI ought to be failing.

    Deliberately NOT an equality or a range check against live coverage: the
    measured figure moves on every test PR, and a test that fails on honest
    green work is a test that gets deleted rather than obeyed.
    """
    measured = thresholds["measured_percent"]
    gate = thresholds["min_line_coverage_percent"]
    assert measured >= gate, (
        f"measured_percent {measured} is below the {gate}% gate — either the snapshot is "
        "wrong or CI should be red"
    )


def test_the_tuning_snapshot_matches_the_thresholds_snapshot(thresholds: dict, tuning: dict):
    """The headroom rule in `auto-qa-tuning.json` reads `observed_percent`; it must be the same reading.

    Pinned to the thresholds file, not to reality: both go stale together, but they
    cannot tell a maintainer two different headrooms.
    """
    assert tuning["coverage"]["observed_percent"] == thresholds["measured_percent"], (
        f"{_TUNING.name} observed_percent {tuning['coverage']['observed_percent']} disagrees with "
        f"{_THRESHOLDS.name} measured_percent {thresholds['measured_percent']}"
    )


@pytest.mark.parametrize("path", sorted(_SNAPSHOT_PROSE))
def test_every_prose_copy_of_the_snapshot_matches_the_thresholds_file(path: str, thresholds: dict):
    phrase = _SNAPSHOT_PROSE[path].format(measured=_percent(thresholds["measured_percent"]))
    assert phrase in _prose(path), (
        f"{path} no longer says {phrase!r}; {_THRESHOLDS.name} is the copy to update first, "
        "then the prose that quotes it"
    )


def test_the_snapshot_says_how_to_regenerate_itself(thresholds: dict):
    """The durable half. #63 happened because the number had no refresh recipe.

    Asserted against docs/metrics.md too: the command has to be the same one, or
    the two files drift into reporting different measurements of 'coverage'.
    """
    command = thresholds["regenerate"]
    assert "--cov=goodreads_mcp" in command, f"regenerate command looks wrong: {command!r}"
    assert command in _METRICS.read_text(encoding="utf-8"), (
        "docs/metrics.md does not contain the same regenerate command, so the two files "
        "can report different things by the same name"
    )


def test_the_note_records_that_the_snapshot_decays(thresholds: dict):
    """Without this sentence the next reader treats a stale number as current."""
    note = thresholds["note"].lower()
    assert "stale" in note, f"the note no longer warns the snapshot decays: {thresholds['note']!r}"
    assert "floor" in note, "the note lost the reason the gate sits below the measurement"


def test_the_test_count_row_matches_what_pytest_collects(collected: dict[str, int]):
    """#85: the row read 322 while the suite ran 418, and no test looked at it.

    #377: the row used to be the exact count, so every PR that added a test
    edited this one line, and each merge put every other open test-adding PR
    in conflict (38 of 108 commits on main in ten days changed it). It is now a
    floor: the offline count must sit in `[floor, floor * 1.1)`. A PR edits the
    row only when the suite outgrows the band (about 170 tests at 1,700) or
    shrinks below the floor, and a reading more than 10% low still fails, so
    #85 cannot recur. The live count stays exact; it moves only when a tool is
    added, and that recipe edits the row anyway.

    Counts, not outcomes: the row's split into passing and skipped holds because
    `tests/e2e` is the only suite that skips itself (without `GOODREADS_LIVE`)
    and CI would be red if an offline test failed. Add a skip outside
    `tests/e2e` and the row's wording, not just its number, needs revisiting.
    """
    assert collected["live"], f"{_LIVE_DIR} collects nothing; the row's skipped half has no source"
    offline, live = collected["offline"], collected["live"]
    suggested = _COUNT_ROW.format(floor=offline // _COUNT_STEP * _COUNT_STEP, live=live)
    match = _COUNT_ROW_RE.search(_prose("docs/quality.md"))
    assert match, (
        "docs/quality.md has no test-count row in the expected wording. It should read:\n"
        f"  {suggested}\n"
        "If the wording changed on purpose, update _COUNT_ROW and _COUNT_ROW_RE here to match."
    )
    floor = int(match["floor"].replace(",", ""))
    in_band = floor <= offline < floor * _COUNT_BAND
    assert in_band and int(match["live"]) == live, (
        f"docs/quality.md's test-count row is stale. pytest collects {offline} offline and "
        f"{live} live tests; the row says {floor:,}+ and {match['live']}, and the offline "
        f"count must be at least the floor and under {_COUNT_BAND}x it. The row should read:\n"
        f"  {suggested}"
    )


def test_the_two_ci_run_rows_agree_with_each_other():
    """Two hand-maintained copies of one reading; neither was checked before (#85).

    Pinned to each other, not to the API: re-reading `gh run list` here would
    make the suite depend on the network and on a window that moves every push.
    """
    metrics = _METRICS_CI_ROW.search(_prose("docs/metrics.md"))
    assert metrics, "docs/metrics.md no longer has a `last N ci.yml runs` row for quality.md to agree with"
    quality = _QUALITY_CI_ROW.search(_prose("docs/quality.md"))
    assert quality, "docs/quality.md no longer has a `CI, last N runs` row"

    assert quality["window"] == metrics["window"], (
        f"docs/quality.md reads the last {quality['window']} runs but docs/metrics.md reads "
        f"the last {metrics['window']}; the same row cannot be both"
    )

    tallies = dict(
        (label, int(count)) for count, label in _COUNT_AND_LABEL.findall(metrics["counts"])
    )
    assert "success" in tallies, f"docs/metrics.md's CI row records no success count: {metrics['counts']!r}"
    assert int(quality["success"]) == tallies["success"], (
        f"docs/quality.md says {quality['success']} successful runs but docs/metrics.md says "
        f"{tallies['success']} over the same window; docs/metrics.md is the fuller copy, so update it "
        "first and then the copy in docs/quality.md"
    )
    assert sum(tallies.values()) == int(metrics["window"]), (
        f"docs/metrics.md's CI row tallies {sum(tallies.values())} runs over a window of "
        f"{metrics['window']}: {tallies}. A partial refresh leaves the row describing two different reads."
    )
