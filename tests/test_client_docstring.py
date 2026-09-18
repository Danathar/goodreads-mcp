"""`client.py`'s module docstring lists the data surfaces; keep it equal to AGENTS.md's.

The list of unofficial Goodreads read surfaces is written down in several
places — `AGENTS.md` ("The data surfaces"), the check-live-endpoints skill,
the Cursor rule — and the module docstring at the top of `client.py` is the
copy a reader meets first when they open the code. It said "four" and listed
four for long enough to be recorded as a known defect while every other copy
said five (#81): the one it dropped was the scraped-HTML surface `list_shelves`
rides, which is exactly the surface with the weakest guarantees and so the one
a reader most needs told about.

`AGENTS.md` is the source of truth (every other agent config file points at
it), so the docstring is pinned to it here in both directions: the counted
word must match the number of entries `AGENTS.md` lists, the docstring's own
numbered entries must run 1..N with no gap, and each entry must be about the
same surface as `AGENTS.md`'s entry in that position — order is the "in order
of robustness" claim both lists make, so a reordering is a drift too.
"""

from __future__ import annotations

from pathlib import Path
import re

import goodreads_mcp.client as client

_ROOT = Path(__file__).resolve().parent.parent
_AGENTS = _ROOT / "AGENTS.md"

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _agents_surfaces() -> list[str]:
    agents = _AGENTS.read_text(encoding="utf-8")
    section = agents.split("## The data surfaces", 1)[1].split("\n## ", 1)[0]
    numbered = re.findall(r"^(\d+)\. \*\*(.+?)\*\*", section, re.M)
    assert [n for n, _ in numbered] == [str(i) for i in range(1, len(numbered) + 1)]
    return [name for _, name in numbered]


def _docstring_surfaces() -> tuple[int, list[str]]:
    doc = client.__doc__ or ""
    word = re.search(r"rides\s+on (\w+) unofficial-but-stable read surfaces", doc)
    assert word, "the docstring no longer states how many surfaces it rides on"
    claimed = _NUMBER_WORDS[word.group(1)]
    # "  1. Shelf RSS feeds   — ..." — the name is everything before the dash.
    numbered = re.findall(r"^\s+(\d+)\. (.+?)\s+—", doc, re.M)
    assert [n for n, _ in numbered] == [str(i) for i in range(1, len(numbered) + 1)]
    return claimed, [name for _, name in numbered]


def _key(name: str) -> str:
    """The first word of a surface name, lowercased: 'Shelf RSS feeds' and
    'Shelf RSS' are the same surface; 'Scraped HTML' is not 'AppSync GraphQL'."""
    return name.split()[0].lower()


def test_the_docstring_counts_as_many_surfaces_as_agents_md_lists():
    claimed, listed = _docstring_surfaces()
    assert claimed == len(listed), (
        f"client.py says it rides on {claimed} surfaces but lists {len(listed)}"
    )
    assert claimed == len(_agents_surfaces()), (
        f"client.py says {claimed} surfaces; AGENTS.md lists {len(_agents_surfaces())}"
    )


def test_the_docstring_lists_the_same_surfaces_in_the_same_order():
    _, listed = _docstring_surfaces()
    assert [_key(n) for n in listed] == [_key(n) for n in _agents_surfaces()]


def test_the_scraped_html_surface_is_named_and_scoped_to_list_shelves():
    """The entry that was missing (#81). It is the only surface with no
    structured equivalent, so its caveat — list_shelves only, do not extend —
    is the part of the list that carries information."""
    doc = client.__doc__ or ""
    entry = re.search(r"^\s+\d+\. Scraped HTML\s+—(.+?)(?=^\s+\d+\. |^\S|\Z)", doc, re.M | re.S)
    assert entry, "the docstring does not list the scraped-HTML surface"
    body = " ".join(entry.group(1).split())
    assert "list_shelves only" in body
    assert "best-effort" in body
    assert "extend" in body
