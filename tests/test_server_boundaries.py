"""Offline tests for server values no other test pinned to an exact number.

Each case here holds a boundary or a constant at the value the code and the
README promise: the annotations and parameter defaults a client reads from
``tools/list``, the get_book language cap, get_reviews' page size and its
``has_more`` on an exactly-filled last page, the discovery walk's
``totalCount`` check, and compare_books' shares when a star bucket is empty.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from goodreads_mcp import server


class _Recorder:
    """Stand-in for ``server.gr.graphql`` that records every call."""

    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(variables)
        if not self.responses:
            raise AssertionError(f"unexpected extra graphql call: {variables!r}")
        return self.responses.pop(0)


@pytest.fixture(scope="module")
def tools() -> dict[str, Any]:
    return {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}


# ------------------------------------------------------ tools/list surface


def test_every_tool_advertises_idempotent_and_open_world(tools):
    """docs/design.md promises idempotent, open-world tools; the client only
    learns that from the annotations."""
    for name, tool in tools.items():
        assert tool.annotations.idempotentHint is True, f"{name} is not idempotent"
        assert tool.annotations.openWorldHint is True, f"{name} is not open-world"


# (tool, parameter, default the client sees). search_books, get_book and
# get_reviews are the defaults the README states.
_DEFAULTS = [
    ("search_books", "max_results", 10),
    ("get_book", "review_language_limit", 5),
    ("get_reviews", "limit", 10),
    ("get_reviews", "sort", "relevance"),
    ("similar_books", "limit", 10),
    ("author_books", "limit", 20),
    ("series_books", "limit", 20),
    ("series_books", "series_index", 0),
    ("get_editions", "limit", 20),
    ("book_lists", "limit", 10),
    ("popular_books", "limit", 20),
]


@pytest.mark.parametrize(("tool", "param", "default"), _DEFAULTS)
def test_tool_parameter_defaults(tools, tool, param, default):
    schema = tools[tool].inputSchema["properties"][param]
    assert schema.get("default") == default


# ---------------------------------------------------------------- get_book


def _language_book(langs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "Book:1": {
            "legacyId": 1,
            "title": "A Book",
            "work": {"stats": {"textReviewsLanguageCounts": langs}},
        }
    }


def test_get_book_accepts_a_zero_language_limit(monkeypatch):
    apollo = _language_book([{"isoLanguageCode": "eng", "count": 3}])
    monkeypatch.setattr(server, "_fetch_book_apollo", lambda book_id: apollo)

    assert server.get_book("1", review_language_limit=0)["review_languages"] is None


def test_get_book_caps_review_languages_at_25(monkeypatch):
    langs = [{"isoLanguageCode": f"l{i:02d}", "count": 100 - i} for i in range(30)]
    apollo = _language_book(langs)
    monkeypatch.setattr(server, "_fetch_book_apollo", lambda book_id: apollo)

    languages = server.get_book("1", review_language_limit=100)["review_languages"]

    assert list(languages) == [f"l{i:02d}" for i in range(25)]


def test_get_book_orders_languages_by_count_and_a_missing_count_last(monkeypatch):
    apollo = _language_book(
        [
            {"isoLanguageCode": "none"},
            {"isoLanguageCode": "one", "count": 1},
            {"isoLanguageCode": "spa", "count": 2},
            {"isoLanguageCode": "eng", "count": 10},
        ]
    )
    monkeypatch.setattr(server, "_fetch_book_apollo", lambda book_id: apollo)

    languages = server.get_book("1", review_language_limit=3)["review_languages"]

    assert list(languages) == ["eng", "spa", "one"]


# ------------------------------------------------------------- get_reviews


_BOOK = {"getBookByLegacyId": {"legacyId": 1, "title": "A Book", "work": {"id": "w"}}}


def _review_page(n: int, total: int) -> dict[str, Any]:
    return {
        "getReviews": {
            "totalCount": total,
            "edges": [{"node": {"rating": 5, "creator": {"name": str(i)}}} for i in range(n)],
            "pageInfo": {"nextPageToken": None},
        }
    }


def test_get_reviews_requests_pages_of_30(monkeypatch):
    """Goodreads derives the offset from the page limit, so it must stay 30."""
    recorder = _Recorder([_BOOK, _review_page(1, 1)])
    monkeypatch.setattr(server.gr, "graphql", recorder)

    server.get_reviews("1", limit=50)

    assert recorder.calls[1]["pagination"] == {"limit": 30}


def test_get_reviews_has_no_more_when_limit_takes_the_last_review(monkeypatch):
    recorder = _Recorder([_BOOK, _review_page(2, 2)])
    monkeypatch.setattr(server.gr, "graphql", recorder)

    result = server.get_reviews("1", limit=2)

    assert result["returned"] == 2
    assert result["has_more"] is False


# ------------------------------------------------- _paginated_graphql_edges


def test_discovery_walk_has_more_when_total_count_is_one_past_what_was_read(monkeypatch):
    recorder = _Recorder(
        [
            {
                "connection": {
                    "totalCount": 3,
                    "edges": [{"node": {"legacyId": i}} for i in range(2)],
                    "pageInfo": {"hasNextPage": False, "nextPageToken": None},
                }
            }
        ]
    )
    monkeypatch.setattr(server.gr, "graphql", recorder)

    edges, has_more, total = server._paginated_graphql_edges("query", "connection", {}, 10)

    assert len(edges) == 2
    assert (has_more, total) == (True, 3)


# ----------------------------------------------------------- compare_books


def test_compare_books_shares_with_empty_positive_buckets(monkeypatch):
    """A zero 4- or 5-star bucket counts as zero, and shares keep one decimal."""
    book = {
        "book_id": "1",
        "average_rating": 2.0,
        "ratings_histogram": {"1": 1, "2": 1, "3": 1, "4": 0, "5": 0},
    }
    monkeypatch.setattr(server, "get_book", lambda bid: book)

    (result,) = server.compare_books(["1"])["books"]

    assert result["pct_positive"] == 0.0
    assert result["pct_critical"] == 66.7


def test_compare_books_rounds_positive_share_to_one_decimal(monkeypatch):
    book = {
        "book_id": "1",
        "average_rating": 4.0,
        "ratings_histogram": {"1": 0, "2": 0, "3": 2, "4": 0, "5": 1},
    }
    monkeypatch.setattr(server, "get_book", lambda bid: book)

    (result,) = server.compare_books(["1"])["books"]

    assert result["pct_positive"] == 33.3
    assert result["pct_critical"] == 0.0
