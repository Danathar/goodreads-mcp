"""get_shelf refuses a shelf name the user does not have (#204).

Goodreads' shelf RSS answers an unknown shelf name with HTTP 200 and the
user's first 100 books from every shelf. Only the channel <title> says what
was served: "Otis 's bookshelf: norway" for the real shelf, "Otis 's
bookshelf: read " (trailing space) for any name the user has no shelf by.

The feeds below are trimmed from live responses for user 1 (Sep 2026): the
channel titles are verbatim, the items are cut to two or three and to the
fields the parser reads.
"""

from __future__ import annotations

import httpx
import pytest

from goodreads_mcp import server
from goodreads_mcp.client import GoodreadsClient

_LONG_SHIPS = """\
  <item>
    <title>The Long Ships</title>
    <link><![CDATA[https://www.goodreads.com/review/show/3634418775?utm_medium=api&utm_source=rss]]></link>
    <book_id>10081041</book_id>
    <author_name>Frans G. Bengtsson</author_name>
    <isbn>159017416X</isbn>
    <user_rating>5</user_rating>
    <user_shelves><![CDATA[adventure, norway, historical-fiction, history]]></user_shelves>
    <average_rating>4.38</average_rating>
    <book_published>1941</book_published>
  </item>
"""

_WE_DIE_ALONE = """\
  <item>
    <title><![CDATA[We Die Alone: A WWII Epic of Escape and Endurance]]></title>
    <link><![CDATA[https://www.goodreads.com/review/show/8823456367?utm_medium=api&utm_source=rss]]></link>
    <book_id>22921276</book_id>
    <author_name>David Howarth</author_name>
    <isbn></isbn>
    <user_rating>4</user_rating>
    <user_shelves><![CDATA[biography, norway, nonfiction, history, world-war-2]]></user_shelves>
    <average_rating>4.35</average_rating>
    <book_published>1954</book_published>
  </item>
"""

# What the whole-library fallback starts with: books from other shelves.
_HOLLYWOOD_ENDING = """\
  <item>
    <title>Hollywood, Ending</title>
    <link><![CDATA[https://www.goodreads.com/review/show/8974259569?utm_medium=api&utm_source=rss]]></link>
    <book_id>250252723</book_id>
    <author_name>John  Green</author_name>
    <isbn></isbn>
    <user_rating>0</user_rating>
    <user_shelves>to-read</user_shelves>
    <average_rating>4.35</average_rating>
    <book_published>2026</book_published>
  </item>
"""

_DEEP_UTOPIA = """\
  <item>
    <title><![CDATA[Deep Utopia: Life and Meaning in a Solved World]]></title>
    <link><![CDATA[https://www.goodreads.com/review/show/7583426988?utm_medium=api&utm_source=rss]]></link>
    <book_id>247586942</book_id>
    <author_name>Nick Bostrom</author_name>
    <isbn></isbn>
    <user_rating>0</user_rating>
    <user_shelves>currently-reading</user_shelves>
    <average_rating>4.00</average_rating>
    <book_published></book_published>
  </item>
"""


def _feed(channel_head: str, *items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" >\n'
        "  <channel>\n"
        f"{channel_head}"
        f"{''.join(items)}"
        "  </channel>\n"
        "</rss>\n"
    )


def _head(title: str, shelf: str) -> str:
    return (
        f"    <title>{title}</title>\n"
        "    <link><![CDATA[https://www.goodreads.com/review/list_rss/1"
        f"?shelf={shelf}&page=1]]></link>\n"
    )


# shelf=norway: a custom shelf user 1 has
NORWAY_FEED = _feed(
    _head("Otis 's bookshelf: norway", "norway"), _LONG_SHIPS, _WE_DIE_ALONE
)
# shelf=read: an exclusive shelf
READ_FEED = _feed(_head("Otis 's bookshelf: read", "read"), _LONG_SHIPS, _WE_DIE_ALONE)
# shelf=definitely-not-a-shelf-xyz (and Norway, historical fiction): the
# whole library, titled "read" with a trailing space
FALLBACK_FEED = _feed(
    _head("Otis 's bookshelf: read ", "definitely-not-a-shelf-xyz"),
    _HOLLYWOOD_ENDING,
    _DEEP_UTOPIA,
    _LONG_SHIPS,
)
# shelf= (empty): the whole library, titled "all"
ALL_FEED = _feed(_head("Otis 's bookshelf: all", ""), _HOLLYWOOD_ENDING, _DEEP_UTOPIA)


def _get_shelf(feed: str, shelf: str, monkeypatch) -> list[dict]:
    monkeypatch.setattr(
        server.gr, "get", lambda url, **kw: httpx.Response(200, text=feed)
    )
    return server.get_shelf(shelf, user_id="1")


# ------------------------------------------------------------ parse_shelf_rss


@pytest.mark.parametrize(
    ("feed", "served"),
    [
        (NORWAY_FEED, "norway"),
        (READ_FEED, "read"),
        (FALLBACK_FEED, "read "),
        (ALL_FEED, "all"),
    ],
)
def test_parse_shelf_rss_reports_the_served_shelf_verbatim(feed, served):
    """The trailing space on the fallback title is the signal; stripping it
    would make an unknown name look like a request for 'read'."""
    assert GoodreadsClient.parse_shelf_rss(feed)[0] == served


def test_parse_shelf_rss_takes_the_text_after_the_last_bookshelf_marker():
    feed = _feed(_head("bookshelf: fan's bookshelf: sci-fi", "sci-fi"), _LONG_SHIPS)
    assert GoodreadsClient.parse_shelf_rss(feed)[0] == "sci-fi"


@pytest.mark.parametrize(
    "channel_head",
    [
        pytest.param("", id="no-title"),
        pytest.param("    <title></title>\n", id="empty-title"),
        pytest.param("    <title>Goodreads: read</title>\n", id="other-shape"),
    ],
)
def test_parse_shelf_rss_reports_no_served_shelf_for_an_unknown_title(channel_head):
    """Only the channel's own <title> counts, not an item's."""
    item = _LONG_SHIPS.replace("The Long Ships", "Otis 's bookshelf: read ")
    served, items = GoodreadsClient.parse_shelf_rss(_feed(channel_head, item))
    assert served is None
    assert [i["book_id"] for i in items] == ["10081041"]


# ------------------------------------------------------------------ get_shelf


@pytest.mark.parametrize(
    ("feed", "shelf"),
    [
        pytest.param(NORWAY_FEED, "norway", id="custom"),
        pytest.param(READ_FEED, "read", id="exclusive"),
    ],
)
def test_get_shelf_returns_the_items_of_a_shelf_the_user_has(feed, shelf, monkeypatch):
    items = _get_shelf(feed, shelf, monkeypatch)
    assert [i["title"] for i in items] == [
        "The Long Ships",
        "We Die Alone: A WWII Epic of Escape and Endurance",
    ]
    assert items[0]["link"].startswith("https://www.goodreads.com/review/show/")


@pytest.mark.parametrize(
    "shelf", ["definitely-not-a-shelf-xyz", "Norway", "historical fiction"]
)
def test_get_shelf_refuses_a_name_the_user_has_no_shelf_by(shelf, monkeypatch):
    """Before #204 these returned the whole library as if it were the shelf."""
    with pytest.raises(ValueError) as excinfo:
        _get_shelf(FALLBACK_FEED, shelf, monkeypatch)
    message = str(excinfo.value)
    assert f"no shelf named {shelf!r}" in message
    assert "case-sensitive" in message
    assert "list_shelves" in message


def test_get_shelf_with_an_empty_name_lists_every_shelf(monkeypatch):
    """shelf='' is served as the whole library titled 'all', as before #204."""
    items = _get_shelf(ALL_FEED, "", monkeypatch)
    assert [i["title"] for i in items] == [
        "Hollywood, Ending",
        "Deep Utopia: Life and Meaning in a Solved World",
    ]


def test_get_shelf_refuses_all_as_a_shelf_name(monkeypatch):
    """'all' is not a shelf name; Goodreads serves it as the unknown-name fallback."""
    with pytest.raises(ValueError, match="no shelf named 'all'"):
        _get_shelf(FALLBACK_FEED, "all", monkeypatch)


@pytest.mark.parametrize(
    "channel_head",
    [
        pytest.param("", id="no-title"),
        pytest.param("    <title>Goodreads: read</title>\n", id="other-shape"),
    ],
)
def test_get_shelf_passes_items_through_when_the_title_says_nothing(
    channel_head, monkeypatch
):
    """No recognisable title means no signal; keep returning the feed."""
    items = _get_shelf(_feed(channel_head, _LONG_SHIPS), "Norway", monkeypatch)
    assert [i["book_id"] for i in items] == ["10081041"]
