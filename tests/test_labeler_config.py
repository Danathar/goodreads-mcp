"""`.github/labeler.yml` labels the paths it names, and every path it names exists.

The labeler applies the path labels docs/risk-tiers.md and docs/maintenance.md
tell a reviewer to read (`client`, `server`, `live-tests`, `ci`, and the
agent-boundary files under `agent-config`). The file's own header says each
rule must match this repository's layout, checked against `git ls-files`, but
nothing did: a misspelt glob (`goodreads_mcp/clinet.py`), a dropped line
(`.claude/**`), a renamed label or a different matcher key all passed the
suite, and the labeler then silently stops applying that label.

The file is read as text, like the workflow tests: the test extra ships no
YAML parser. Any line the reader below does not recognise fails, so a shape
this file does not know cannot slip past unread. Globs are matched the way
actions/labeler v5 matches them (minimatch with `dot: true`), restricted to
the two forms the file uses: a literal path and a `dir/**` prefix.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_LABELER = _ROOT / ".github" / "labeler.yml"

_LABEL = re.compile(r"^(?P<label>[a-z][a-z-]*):$")
_CHANGED_FILES = re.compile(r"^  - changed-files:$")
_ANY_INLINE = re.compile(r"^      - any-glob-to-any-file: '(?P<glob>[^']+)'$")
_ANY_BLOCK = re.compile(r"^      - any-glob-to-any-file:$")
_ITEM = re.compile(r"^          - '(?P<glob>[^']+)'$")
_GLOB = re.compile(r"^[\w.-]+(?:/[\w.-]+)*(?:/\*\*)?$")

# One representative path per rule, and the full label set the labeler must
# give it. Each path is checked to be tracked, so a row cannot outlive the
# file it describes.
_EXPECTED = {
    "goodreads_mcp/server.py": {"server"},
    "goodreads_mcp/client.py": {"server", "client"},
    "tests/test_parsers.py": {"tests"},
    "tests/e2e/test_smoke_live.py": {"tests", "live-tests"},
    ".github/workflows/ci.yml": {"ci"},
    ".github/policies/workflow-permissions.json": {"ci"},
    ".github/auto-qa-tuning.json": {"ci"},
    ".coverage-thresholds.json": {"ci"},
    "AGENTS.md": {"agent-config"},
    "CLAUDE.md": {"agent-config"},
    ".claude/settings.json": {"agent-config"},
    ".claude/hooks/guard-bash.py": {"agent-config"},
    ".github/copilot-instructions.md": {"agent-config"},
    "docs/risk-tiers.md": {"docs"},
    "README.md": {"docs"},
    "CONTRIBUTING.md": {"docs"},
    "pyproject.toml": {"packaging"},
    "manifest.json": {"packaging"},
    "server.json": {"packaging"},
    ".mcpbignore": {"packaging"},
    # Paths no rule names get no label at all.
    "LICENSE": set(),
    ".github/labeler.yml": set(),
}


def _rules() -> dict[str, list[str]]:
    """Label -> globs, read strictly in the one shape the file uses."""
    rules: dict[str, list[str]] = {}
    label = None
    state = None
    for n, line in enumerate(_LABELER.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        if m := _LABEL.match(line):
            label = m["label"]
            assert label not in rules, f"labeler.yml:{n}: {label} defined twice"
            rules[label] = []
            state = "label"
        elif _CHANGED_FILES.match(line) and state == "label":
            state = "changed-files"
        elif (m := _ANY_INLINE.match(line)) and state == "changed-files":
            rules[label].append(m["glob"])
            state = "done"
        elif _ANY_BLOCK.match(line) and state == "changed-files":
            state = "items"
        elif (m := _ITEM.match(line)) and state == "items":
            rules[label].append(m["glob"])
        else:
            pytest.fail(f"labeler.yml:{n}: unrecognised line {line!r}")
    return rules


def _matches(glob: str, path: str) -> bool:
    if glob.endswith("/**"):
        return path.startswith(glob[:-2])
    return path == glob


def _labels_for(path: str) -> set[str]:
    return {label for label, globs in _rules().items() if any(_matches(g, path) for g in globs)}


@pytest.fixture(scope="module")
def tracked() -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    files = result.stdout.splitlines()
    assert files, "git ls-files returned nothing"
    return files


def test_every_rule_is_one_any_glob_to_any_file_list():
    rules = _rules()
    assert set(rules) == {label for labels in _EXPECTED.values() for label in labels}
    for label, globs in rules.items():
        assert globs, f"{label} names no path"
        for glob in globs:
            assert _GLOB.match(glob), f"{label}: {glob!r} is not a literal path or dir/** glob"


def test_every_glob_matches_a_tracked_file(tracked: list[str]):
    """The header's own rule: "verify against `git ls-files`"."""
    for label, globs in _rules().items():
        for glob in globs:
            assert any(_matches(glob, p) for p in tracked), f"{label}: {glob} matches no tracked file"


@pytest.mark.parametrize("path", sorted(_EXPECTED))
def test_each_path_gets_exactly_its_labels(path: str, tracked: list[str]):
    assert path in tracked, f"{path} is not tracked; update the table"
    assert _labels_for(path) == _EXPECTED[path]


def test_the_matcher_is_a_prefix_on_a_path_boundary():
    assert _matches("tests/e2e/**", "tests/e2e/test_smoke_live.py")
    assert not _matches("tests/e2e/**", "tests/e2e_helpers.py")
    assert not _matches("tests/e2e/**", "tests/test_e2e.py")
    assert _matches("README.md", "README.md")
    assert not _matches("README.md", "docs/README.md")


def test_every_tracked_package_and_workflow_file_is_labelled(tracked: list[str]):
    """The labels the tier docs lean on reach every file they are meant to."""
    for path in tracked:
        labels = _labels_for(path)
        if path.startswith("goodreads_mcp/"):
            assert "server" in labels, path
        if path.startswith(".github/workflows/"):
            assert "ci" in labels, path
        if path.startswith("tests/e2e/"):
            assert "live-tests" in labels, path
        if path.startswith(".claude/") or path.startswith("prompts/"):
            assert "agent-config" in labels, path
