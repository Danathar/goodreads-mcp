"""The coverage gate is written down three times; these keep the copies honest.

`.coverage-thresholds.json` records the gate and a measured snapshot, and until
now **nothing read it** — not CI, not a test, only `.github/labeler.yml` matching
its path. A file nobody reads is a file nobody updates, which is how its note
came to claim ~61% while the suite was actually at 82% (#63).

Two different decay rates, so two different treatments:

- **The gate** (`--cov-fail-under`) is a constant that appears in
  `.github/workflows/ci.yml`, in `.coverage-thresholds.json`, and in
  `docs/metrics.md`. Three copies of one number is a drift trap with an exact
  answer, so it is pinned here: change the gate in CI and forget a doc, and this
  goes red naming the file you missed.
- **The measured figure** is a reading of HEAD and moves whenever a test lands.
  It cannot be pinned without running coverage inside the suite being measured,
  which would be both slow and circular. So it is only checked for COHERENCE —
  a snapshot below the gate would be asserting that CI ought to be failing — and
  the durable part, the command to regenerate it, is asserted to be present.

That split is the point. Pinning the unpinnable number would produce a test that
fails on every honest test PR, which is how a guard gets deleted.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_THRESHOLDS = _ROOT / ".coverage-thresholds.json"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_METRICS = _ROOT / "docs" / "metrics.md"

# `--cov-fail-under=55`, `--cov-fail-under 55`, or `--cov-fail-under=55.0`.
_CI_GATE = re.compile(r"--cov-fail-under[= ](\d+(?:\.\d+)?)")


@pytest.fixture(scope="module")
def thresholds() -> dict:
    return json.loads(_THRESHOLDS.read_text(encoding="utf-8"))


def _ci_gate() -> float:
    """The gate CI actually enforces, read from the workflow that enforces it."""
    matches = _CI_GATE.findall(_CI.read_text(encoding="utf-8"))
    assert matches, f"{_CI.name} no longer passes --cov-fail-under; the gate this file pins is gone"
    assert len(set(matches)) == 1, f"{_CI.name} passes conflicting gates: {sorted(set(matches))}"
    return float(matches[0])


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


def test_metrics_doc_quotes_the_same_gate():
    """docs/metrics.md is the third copy, and the one a human reads first."""
    gate = _ci_gate()
    text = _METRICS.read_text(encoding="utf-8")
    # Rendered as an integer when it is one: "55%", not "55.0%".
    rendered = f"{int(gate)}%" if gate.is_integer() else f"{gate}%"
    assert f"| coverage gate | {rendered} (`--cov-fail-under`) |" in text, (
        f"docs/metrics.md's coverage-gate row no longer reads {rendered}, which is what "
        f"{_CI.name} enforces"
    )


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
