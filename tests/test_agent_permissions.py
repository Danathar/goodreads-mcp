"""Pin the agent permission rules, and what the docs claim they mean.

`.claude/settings.json` is the only thing standing between an agent working in
this checkout and the developer's machine. Nothing read it until now, so it
could change -- or its documented meaning could drift away from it -- with no
test noticing. That drift is exactly what produced #60: `.claude/README.md`
called the allow list "read-only and routine" when three of its five entries
are not read-only at all, and the wrong description is what made the gap
invisible for as long as it was.

Two things are pinned here, neither of which needs the guard hook #60 calls for:

* **The rule inventory.** A verb added to `allow` -- or a `deny` entry quietly
  dropped -- fails these tests. `_ALLOW_REACH` forces the addition to come with
  a written answer to "what does this reach?", which is the question the three
  existing offenders were never asked.
* **The documented limits.** `docs/SECURITY-AI.md` now records that the
  prefix-match limit cuts both ways and that the allow side is the sharper
  edge. A later edit that reverts it to the comfortable version goes red here.

What is deliberately NOT pinned: whether a given command is actually blocked.
It is not -- the `PreToolUse` guard from #60 does not exist yet, so
`pytest /tmp/anything.py` still runs. Tables of denied and permitted spellings
belong in this file, but they can only be written once there is something to
deny them. Until then these tests say what the rules *are*, not that they are
sufficient, and #60 stays open.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS = _ROOT / ".claude" / "settings.json"
_SECURITY_AI = _ROOT / "docs" / "SECURITY-AI.md"
_CLAUDE_README = _ROOT / ".claude" / "README.md"

# Every entry on the allow list, against what it can actually reach. The note is
# the point of the table: an entry cannot be added without writing one, and the
# three that are not read-only say so rather than being filed under the verb a
# reader expects. Asserted exhaustive in both directions below.
#
# Sources for the three non-obvious notes, all reproduced at ff8c574 (#60):
#   pytest    -- a path on the command line overrides `testpaths` and is collected
#                regardless of the `test_*.py` convention; import runs module-level
#                code, and code inside a Python process is not a tool call.
#   git diff  -- `--no-index a b` diffs two arbitrary paths with no repository;
#                `--output=<path>` writes one.
#   git log   -- `--output=<path>` is accepted here too.
_ALLOW_REACH = {
    "Read": "the Read tool; narrowed by the Read(./.env) deny entries",
    "Bash(pytest *)": "arbitrary code execution -- pytest imports what it collects",
    "Bash(git status *)": "read-only in itself; a shell redirection still writes",
    "Bash(git diff *)": "arbitrary file read (--no-index) and write (--output=)",
    "Bash(git log *)": "arbitrary file write (--output=)",
}

_ASK = {
    "Bash(git push *)",
    "Edit(./.github/workflows/**)",
    "Write(./.github/workflows/**)",
}

_DENY = {
    "Bash(git push --force)",
    "Bash(git push --force *)",
    "Bash(git push -f)",
    "Bash(git push -f *)",
    "Bash(git reset --hard)",
    "Bash(git reset --hard *)",
    "Read(./.env)",
    "Read(./.env.*)",
}


def _permissions() -> dict[str, list[str]]:
    return json.loads(_SETTINGS.read_text(encoding="utf-8"))["permissions"]


def test_the_settings_file_is_committed_and_parses():
    """A rule file that has been renamed away guards nothing, silently."""
    assert _SETTINGS.is_file(), f"{_SETTINGS} is missing"
    assert set(_permissions()) >= {"allow", "ask", "deny"}


def test_the_allow_list_is_the_recorded_set():
    """A new allow verb must arrive with a note saying what it reaches.

    Exhaustive in both directions: an entry the table does not cover is an
    unexamined grant, and a table row with no rule behind it is a stale note
    that reads as coverage.
    """
    allowed = set(_permissions()["allow"])
    assert allowed == set(_ALLOW_REACH), (
        "the allow list and its reach table disagree: "
        f"ungrafted={sorted(allowed - set(_ALLOW_REACH))}, "
        f"noted but not granted={sorted(set(_ALLOW_REACH) - allowed)}"
    )


@pytest.mark.parametrize("entry", sorted(_ALLOW_REACH))
def test_every_allow_entry_records_what_it_reaches(entry: str):
    """An empty note records no decision; it only silences the test above."""
    assert _ALLOW_REACH[entry].strip(), f"{entry} has no reach note"


def test_the_ask_list_is_the_recorded_set():
    assert set(_permissions()["ask"]) == _ASK


def test_the_deny_list_is_the_recorded_set():
    """Both spellings of each destructive command, and both `.env` reads.

    Prefix matching is why each destructive command is listed bare *and* with a
    trailing `*`; dropping either half is a silent narrowing.
    """
    assert set(_permissions()["deny"]) == _DENY


def test_the_test_hook_is_still_registered():
    """The PostToolUse hook is the one piece of settings.json that runs code."""
    hooks = json.loads(_SETTINGS.read_text(encoding="utf-8")).get("hooks", {})
    matchers = [entry.get("matcher") for entry in hooks.get("PostToolUse", [])]
    assert "Write|Edit" in matchers, f"PostToolUse matchers are {matchers}"


# ------------------------------------------------ what the docs claim (#60)


def test_security_ai_records_that_the_prefix_limit_cuts_both_ways():
    """The comfortable version of this paragraph is what hid #60.

    It described only what the *deny* list cannot catch, and concluded that the
    ask on every push is the real gate. The allow side is the sharper edge, and
    a push from inside a process the allow list started is not a tool call.
    """
    text = _SECURITY_AI.read_text(encoding="utf-8")
    for claim in (
        "cuts both ways",
        "allow** side is the sharper edge",
        "as tool calls",
    ):
        assert claim in text, f"docs/SECURITY-AI.md no longer says {claim!r}"


def test_security_ai_still_points_at_the_claude_readme():
    """The two documents are one explanation; a dangling pointer splits it."""
    assert "../.claude/README.md" in _SECURITY_AI.read_text(encoding="utf-8")
    assert _CLAUDE_README.is_file()
