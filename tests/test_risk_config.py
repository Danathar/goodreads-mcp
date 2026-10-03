"""Join `.claude/risk-config.json` to `docs/risk-tiers.md` so they cannot drift.

The config restates the doc's tiers as data. Nothing else reads it, so a later
edit to either side would leave the other quietly wrong: a path moved between
tiers in the doc but not the config, or a Tier 1 evidence command reworded in
the doc while the config still names the old one. That is the worst kind of
drift here, since Tier 1 is the tier whose evidence a green offline suite does
not replace.

The doc is the source. This test reads its tier headings, each tier's
`Covers:` paragraph, its `**Required:**` commands and its out-of-scope bullets,
and compares them to the config by value (not by truthiness). It also checks
that every listed path exists, so a rename in the tree is caught.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DOC = (_ROOT / "docs" / "risk-tiers.md").read_text(encoding="utf-8")
_CONFIG = json.loads((_ROOT / ".claude" / "risk-config.json").read_text(encoding="utf-8"))

_PATH_LIKE = re.compile(r"/|\.(?:py|toml|json|md|yml|yaml)$")


def _section(heading_re: str) -> str:
    match = re.search(rf"^## {heading_re}.*?(?=^## |\Z)", _DOC, re.S | re.M)
    assert match, heading_re
    return match.group(0)


def _doc_tiers() -> dict[int, tuple[str, str]]:
    """tier number -> (name, section text), from the `## Tier N — name` headings."""
    found = {}
    for match in re.finditer(r"^## Tier (\d) — (.+)$", _DOC, re.M):
        found[int(match.group(1))] = (match.group(2).strip(), _section(re.escape(match.group(0)[3:])))
    return found


def _covers(section: str) -> str:
    match = re.search(r"^Covers:(.*?)(?:\n\n|\Z)", section, re.S | re.M)
    assert match, "tier section has no Covers paragraph"
    return " ".join(match.group(1).split())


def _doc_paths(covers: str) -> set[str]:
    return {t for t in re.findall(r"`([^`]+)`", covers) if _PATH_LIKE.search(t)}


def _doc_commands(section: str) -> list[str]:
    match = re.search(r"\*\*Required:\*\*\n(.*?)(?:\n\n|\n###|\Z)", section, re.S)
    if not match:
        return []
    spans = re.findall(r"`([^`]+)`", match.group(1))
    return [s for s in spans if re.match(r"(?:\w+=\S+ )*pytest\b", s)]


_TIERS = _doc_tiers()


def test_tier_numbers_and_names_match_the_doc_headings():
    config = {t["tier"]: t["name"] for t in _CONFIG["tiers"]}
    assert config == {n: name for n, (name, _) in _TIERS.items()}
    assert sorted(config) == [1, 2, 3]


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_paths_are_exactly_those_the_covers_text_names(tier):
    entry = next(t for t in _CONFIG["tiers"] if t["tier"] == tier)
    covers = _covers(_TIERS[tier][1])
    assert entry["paths"], "tier has no paths"
    assert set(entry["paths"]) == _doc_paths(covers)
    for path in entry["paths"]:
        assert f"`{path}`" in covers, path


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_required_commands_equal_the_docs_required_list(tier):
    entry = next(t for t in _CONFIG["tiers"] if t["tier"] == tier)
    assert entry["required_commands"] == _doc_commands(_TIERS[tier][1])


def test_tier_one_command_is_the_live_e2e_run():
    # Guards the extractor itself: if the doc's wording changed so that no
    # command parses, the equality test above would pass on two empty lists.
    assert _doc_commands(_TIERS[1][1]) == ["GOODREADS_LIVE=1 pytest tests/e2e -v"]
    assert _doc_commands(_TIERS[2][1]) == ["pytest -q"]


def test_out_of_scope_list_equals_the_docs_bullets():
    block = _section("Out of scope")
    bullets = re.findall(r"^- (.+)$", block, re.M)
    assert bullets
    assert _CONFIG["out_of_scope"] == bullets


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_every_listed_path_exists(tier):
    entry = next(t for t in _CONFIG["tiers"] if t["tier"] == tier)
    for path in entry["paths"]:
        if any(c in path for c in "*?["):
            assert list(_ROOT.glob(path)), f"{path} matches nothing"
        else:
            assert (_ROOT / path).exists(), path


def test_doc_says_the_config_is_input_not_a_classifier():
    applying = _section("Applying this")
    assert "no automated classifier" in applying
    assert ".claude/risk-config.json" in applying
