"""Every GitHub Action the workflows call is pinned to a commit, and stays movable.

#260 found every `uses:` line naming a floating tag (`@v4`, `@release/v1`). A
tag can be moved by whoever controls the action's repository, and the next run
here would execute the new code with no diff in this repo. #264 pinned all of
them to the commit each tag pointed to, with the tag kept as a trailing
`# vN` comment, and #263 added `.github/dependabot.yml` so the pins get bump
proposals instead of freezing.

Nothing checked either half (#265). A new step written the usual way
(`uses: actions/cache@v4`) passed the suite, and so did a hand bump that moved
one copy of `actions/checkout` and left the other seven behind.

Lines are read as text, like the other workflow tests: the test extra ships no
YAML parser. Any line that says `uses:` must match the pinned shape exactly, so
a spelling this file does not know (quotes, `docker://`, a local `./` action)
fails rather than slipping past.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEPENDABOT = _ROOT / ".github" / "dependabot.yml"

# Workflows whose only job is a `gh` call on the runner's preinstalled CLI, so
# they have nothing to pin. Every other workflow must still show up in the
# scan: a workflow renamed or moved out of it would otherwise stop being checked.
_RUNS_NO_ACTION = {"auto-issues.yml"}

_USES = re.compile(r"^\s*(?:- )?uses:")
_PINNED = re.compile(
    r"^\s*(?:- )?uses: (?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)"
    r"@(?P<sha>[0-9a-f]{40}) # (?P<tag>v\d+(?:\.\d+){0,2})\s*$"
)


def _workflow_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", ".github/workflows/*.yml", ".github/workflows/*.yaml"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [_ROOT / p for p in out.splitlines()]


def _uses_lines() -> list[tuple[str, str]]:
    """(file:line, text) for every non-comment line that says `uses:`."""
    found = []
    for path in _workflow_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _USES.match(line):
                found.append((f"{path.name}:{number}", line))
    return found


def test_the_scan_reaches_every_workflow_and_finds_the_known_actions():
    names = {path.name for path in _workflow_files()}
    assert {"ci.yml", "release.yml", "nightly-compliance.yml", "ai-fix.yml", "labeler.yml"} <= names
    seen = {where.split(":")[0] for where, _ in _uses_lines()}
    unexpected = names - seen - _RUNS_NO_ACTION
    assert not unexpected, f"workflows with no uses: line at all: {sorted(unexpected)}"
    # The exemption must not outlive the workflow, or a rename would leave a
    # stale name here and the renamed file would silently be exempt too.
    assert _RUNS_NO_ACTION <= names, f"exempt workflows that no longer exist: {sorted(_RUNS_NO_ACTION - names)}"
    assert not _RUNS_NO_ACTION & seen, "a workflow listed as running no action now has a uses: line; drop it from the set"
    actions = {m.group("action") for _, line in _uses_lines() if (m := _PINNED.match(line))}
    assert {"actions/checkout", "actions/setup-python", "pypa/gh-action-pypi-publish"} <= actions


def test_every_uses_line_is_pinned_to_a_full_commit_sha_with_its_tag_beside_it():
    bad = [f"{where}: {line.strip()}" for where, line in _uses_lines() if not _PINNED.match(line)]
    assert not bad, (
        "pin each action to the full 40-character commit and keep the tag as a"
        " trailing comment, e.g. `uses: actions/checkout@<sha> # v4`:\n" + "\n".join(bad)
    )


def test_each_action_is_pinned_to_one_commit_and_one_tag_everywhere():
    pins: dict[str, set[tuple[str, str]]] = defaultdict(set)
    where: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for location, line in _uses_lines():
        match = _PINNED.match(line)
        if match:
            key = (match.group("sha"), match.group("tag"))
            pins[match.group("action")].add(key)
            where[(match.group("action"), *key)].append(location)
    split = {
        action: {f"{sha[:12]} # {tag}": where[(action, sha, tag)] for sha, tag in keys}
        for action, keys in pins.items()
        if len(keys) > 1
    }
    assert not split, f"one action, two pins (a partial bump, or a SHA moved without its comment): {split}"


def test_one_commit_never_carries_two_tag_comments():
    tags: dict[str, set[str]] = defaultdict(set)
    for _, line in _uses_lines():
        match = _PINNED.match(line)
        if match:
            tags[match.group("sha")].add(match.group("tag"))
    assert not {sha: t for sha, t in tags.items() if len(t) > 1}


def _dependabot_updates(text: str) -> list[dict[str, str]]:
    """Each `updates:` entry's top-level scalar keys, read by indentation."""
    assert re.search(r"^version: 2\s*$", text, re.M), "dependabot.yml must declare version: 2"
    body = text.split("\nupdates:\n", 1)[1]
    entries: list[dict[str, str]] = []
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^  - (\S+): (.+)$", line)
        field = re.match(r"^    ([a-z-]+): (.+)$", line)
        if item:
            entries.append({item.group(1): item.group(2).strip().strip("\"'")})
        elif field and entries:
            entries[-1][field.group(1)] = field.group(2).strip().strip("\"'")
        elif not line.startswith(" "):
            break
    return entries


def test_dependabot_proposes_bumps_for_the_pinned_actions():
    actions = [e for e in _dependabot_updates(_DEPENDABOT.read_text(encoding="utf-8")) if e.get("package-ecosystem") == "github-actions"]
    assert len(actions) == 1, "dependabot.yml needs exactly one github-actions entry, or the pins never move"
    (entry,) = actions
    # "/" is how Dependabot spells "this repository's .github/workflows".
    assert entry.get("directory") == "/"
    assert entry.get("open-pull-requests-limit") != "0", "a limit of 0 turns version updates off"


def test_the_dependabot_parser_keeps_entries_apart_and_skips_nested_keys():
    text = (
        "version: 2\nupdates:\n"
        "  - package-ecosystem: github-actions\n    directory: /\n    schedule:\n      interval: weekly\n"
        "  # a comment\n"
        "  - package-ecosystem: \"pip\"\n    directory: /sub\n"
    )
    assert _dependabot_updates(text) == [
        {"package-ecosystem": "github-actions", "directory": "/"},
        {"package-ecosystem": "pip", "directory": "/sub"},
    ]
