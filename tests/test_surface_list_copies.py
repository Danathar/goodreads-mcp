"""Every page that lists the read surfaces says the shelf feed is answered 404.

Goodreads has answered `/review/list_rss/{uid}` with 404 for every user since
Oct 2026 (#421). `get_shelf` raises `ShelfFeedUnavailable` for it (#423), and
#425 moved the feed to the end of AGENTS.md's "in order of robustness" list and
of the `client.py` docstring, with tests. It also rewrote five other copies of
that list, and four of them — the add-a-tool skill's ranking, the Cursor rule,
the Copilot instructions and the README's first-run step 4 — could be put back
to their old wording with the whole suite green. The add-a-tool skill is the
one #425 was written for: its ranking put the dead feed first and steered new
tools at it. A fifth copy, the README's opening paragraph, was missed and still
listed "RSS feeds" first with no word about the 404.

A copy is found by what it says, not by file name: any sentence in a tracked
Markdown or rule file that names the RSS feed alongside the autocomplete
endpoint and GraphQL is a list of surfaces, and it has to mention the 404. A
paragraph that ranks them "in order of robustness" may not name the feed before
the autocomplete endpoint. A new page that copies the list is held to the same
rule without being named here.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

_ROOT = Path(__file__).resolve().parent.parent

_RSS = re.compile(r"\bRSS\b", re.I)


def _pages() -> list[str]:
    listing = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z", "--", "*.md", "*.mdc"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout
    return sorted(name for name in listing.split("\0") if name)


def _sentences(text: str) -> list[str]:
    """Sentences, with each list item and table row its own block first."""
    found = []
    for block in re.split(r"\n\s*\n|\n(?=\s*(?:[-*|]|\d+\.) )", text):
        squashed = " ".join(block.split())
        found += [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z`*(\[])", squashed) if s]
    return found


def _surface_lists() -> list[tuple[str, str]]:
    return [
        (page, sentence)
        for page in _pages()
        for sentence in _sentences((_ROOT / page).read_text(encoding="utf-8"))
        if _RSS.search(sentence) and "autocomplete" in sentence and "GraphQL" in sentence
    ]


def test_the_sentence_reader_splits_items_rows_and_sentences():
    text = "Intro. Tools ride on RSS and\nGraphQL.\n\n- one\n- two. Three\n| a | b |\n"
    assert _sentences(text) == ["Intro.", "Tools ride on RSS and GraphQL.", "- one", "- two.", "Three", "| a | b |"]


def test_the_surface_lists_are_found_in_every_copy_that_has_one():
    """The finder is what the next two tests stand on: if it stopped matching,
    they would pass on nothing."""
    pages = {page for page, _ in _surface_lists()}
    assert {
        ".cursor/rules/goodreads-mcp.mdc",
        ".github/copilot-instructions.md",
        "README.md",
        "prompts/diagnose-parse-regression.md",
    } <= pages


def test_every_surface_list_says_the_shelf_feed_is_answered_404():
    stale = [f"{page}: {sentence}" for page, sentence in _surface_lists() if "404" not in sentence]
    assert not stale, (
        "these list the shelf RSS feed as a read surface without saying Goodreads "
        "has answered it with 404 since Oct 2026 (#421):\n" + "\n".join(stale)
    )


def test_no_robustness_ranking_puts_the_shelf_feed_above_autocomplete():
    ranked = [
        (page, " ".join(paragraph.split()))
        for page in _pages()
        for paragraph in re.split(r"\n\s*\n", (_ROOT / page).read_text(encoding="utf-8"))
        if "order of robustness" in paragraph and "autocomplete" in paragraph and _RSS.search(paragraph)
    ]
    assert ranked, "no paragraph ranks the surfaces by robustness any more"
    for page, paragraph in ranked:
        assert paragraph.index("autocomplete") < _RSS.search(paragraph).start(), f"{page}: {paragraph}"


def test_the_first_run_shelf_step_expects_the_404():
    """The first-run checklist told every install that `get_shelf` checks the
    user id and the feed, so the 404 read as a wrong `user_id`."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## first-run verification", 1)[1].split("\n## ", 1)[0]
    step = re.search(r"^\d+\. `get_shelf\(.*$", section, re.M)
    assert step, "the first-run checklist no longer has a get_shelf step"
    assert "ShelfFeedUnavailable" in step.group(0) and "404" in step.group(0)
