"""Offline tests for the guards and loop exits the tools reach on bad data.

Every path here is a refusal or a short-circuit: a Goodreads response that is
missing the object the tool needs, an argument the tool rejects before it
touches the network, or the condition that ends a pagination loop. The live
suite never provokes them (Goodreads answers correctly), so without these
tests a broken guard ships silently -- a swallowed error, an unbounded fan-out,
or a loop that never terminates.
"""

from __future__ import annotations

from typing import Any

import pytest

from goodreads_mcp import server


class _Recorder:
    """Stand-in for ``server.gr.graphql`` that records every call."""

    def __init__(self, responses: list[dict[str, Any]] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(variables)
        if not self.responses:
            raise AssertionError(f"unexpected extra graphql call: {variables!r}")
        return self.responses.pop(0)


def _forbidden_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("graphql must not be called")


# --------------------------------------------------------------- _find_book


def test_find_book_raises_when_apollo_state_has_no_book():
    """A WAF/interstitial page parses fine but carries no Book object."""
    with pytest.raises(ValueError, match="No Book object in Apollo state for '54'"):
        server._find_book({"ROOT_QUERY": {"__typename": "Query"}}, "54")


def test_find_book_ignores_a_book_entry_without_a_title():
    """A Book stub with no title is a reference placeholder, not the book."""
    with pytest.raises(ValueError, match="No Book object in Apollo state"):
        server._find_book({"Book:kca://1": {"legacyId": 1}}, "1")


# ------------------------------------------------- _paginated_graphql_edges


def test_paginated_graphql_edges_returns_empty_without_calling_graphql(monkeypatch):
    monkeypatch.setattr(server.gr, "graphql", _forbidden_graphql)

    edges, has_more, total = server._paginated_graphql_edges(
        "query", "connection", {"id": "book"}, 0
    )

    assert edges == []
    assert has_more is False
    assert total is None


def test_paginated_graphql_edges_reports_more_when_a_page_overshoots(monkeypatch):
    """Goodreads may return more edges than asked for; the extras still count."""
    recorder = _Recorder(
        [
            {
                "connection": {
                    "edges": [{"node": {"legacyId": i}} for i in range(5)],
                    "pageInfo": {"hasNextPage": False, "nextPageToken": None},
                }
            }
        ]
    )
    monkeypatch.setattr(server.gr, "graphql", recorder)

    edges, has_more, total = server._paginated_graphql_edges(
        "query", "connection", {}, 2
    )

    assert [e["node"]["legacyId"] for e in edges] == [0, 1]
    assert has_more is True
    assert total is None
    assert len(recorder.calls) == 1


def test_paginated_graphql_edges_stops_when_a_page_token_repeats(monkeypatch):
    """A server that hands back the same cursor must not loop forever."""
    calls: list[dict[str, Any]] = []

    def graphql(query, variables):
        calls.append(variables)
        # The token guard is the exit under test. Refuse a third call so a
        # regression fails here, on the request that should never happen,
        # instead of being masked by the limit exit or running unbounded.
        if len(calls) > 2:
            raise AssertionError(f"unexpected third page request: {variables!r}")
        return {
            "connection": {
                "edges": [{"node": {"legacyId": len(calls)}}],
                "pageInfo": {"hasNextPage": True, "nextPageToken": "same-token"},
            }
        }

    monkeypatch.setattr(server.gr, "graphql", graphql)

    edges, has_more, _ = server._paginated_graphql_edges("query", "connection", {}, 5)

    assert len(calls) == 2, "the repeated cursor must end the loop"
    assert [e["node"]["legacyId"] for e in edges] == [1, 2]
    assert has_more is True


# -------------------------------------------------------- _resolve_book_ids


def test_resolve_book_ids_raises_when_the_book_is_unknown(monkeypatch):
    recorder = _Recorder([{"getBookByLegacyId": None}])
    monkeypatch.setattr(server.gr, "graphql", recorder)

    with pytest.raises(ValueError, match="No book found for id '999'"):
        server._resolve_book_ids("999")

    assert recorder.calls == [{"id": 999}]


# -------------------------------------------------------------- get_reviews


def test_get_reviews_raises_when_the_book_is_unknown(monkeypatch):
    recorder = _Recorder([{"getBookByLegacyId": None}])
    monkeypatch.setattr(server.gr, "graphql", recorder)

    with pytest.raises(ValueError, match="No book found for id '999'"):
        server.get_reviews("999")

    assert len(recorder.calls) == 1, "must not page reviews for a missing book"


def test_get_reviews_raises_when_the_book_has_no_work(monkeypatch):
    """Reviews aggregate over the work; without a work id there is nothing to ask."""
    recorder = _Recorder([{"getBookByLegacyId": {"legacyId": 1, "work": None}}])
    monkeypatch.setattr(server.gr, "graphql", recorder)

    with pytest.raises(ValueError, match="Could not resolve work id for book '1'"):
        server.get_reviews("1")

    assert len(recorder.calls) == 1


def test_get_reviews_stops_when_a_page_has_no_next_token(monkeypatch):
    """Fewer reviews than asked for ends the loop instead of re-fetching page one."""
    recorder = _Recorder(
        [
            {"getBookByLegacyId": {"legacyId": 1, "title": "A Book", "work": {"id": "w"}}},
            {
                "getReviews": {
                    "totalCount": 2,
                    "edges": [
                        {"node": {"rating": 5, "text": "Good", "creator": {"name": "A"}}},
                        {"node": {"rating": 4, "text": "Fine", "creator": {"name": "B"}}},
                    ],
                    "pageInfo": {"nextPageToken": None},
                }
            },
        ]
    )
    monkeypatch.setattr(server.gr, "graphql", recorder)

    result = server.get_reviews("1", limit=10)

    assert result["returned"] == 2
    assert result["total_text_reviews"] == 2
    assert len(recorder.calls) == 2, "exhausted reviews must not trigger another page"


def test_get_reviews_stops_when_a_page_is_empty_despite_a_next_token(monkeypatch):
    """An empty page with a cursor would otherwise page forever."""
    recorder = _Recorder(
        [
            {"getBookByLegacyId": {"legacyId": 1, "title": "A Book", "work": {"id": "w"}}},
            {
                "getReviews": {
                    "totalCount": 7,
                    "edges": [],
                    "pageInfo": {"nextPageToken": "page-2"},
                }
            },
        ]
    )
    monkeypatch.setattr(server.gr, "graphql", recorder)

    result = server.get_reviews("1", limit=10)

    assert result["returned"] == 0
    assert result["reviews"] == []
    assert len(recorder.calls) == 2


# ------------------------------------------------------------- author_books


def test_author_books_raises_when_no_author_can_be_resolved(monkeypatch):
    resolved = {
        "legacy_id": 1,
        "title": "A Book",
        "contributor_kca": None,
        "contributor_name": None,
        "contributor_url": None,
        "series_memberships": [],
    }
    monkeypatch.setattr(server, "_resolve_book_ids", lambda book_id: resolved)
    monkeypatch.setattr(server, "_paginated_graphql_edges", _forbidden_graphql)

    with pytest.raises(ValueError, match="Could not resolve an author for book '1'"):
        server.author_books("1")


# ------------------------------------------------------------- series_books


def test_series_books_rejects_a_negative_series_index(monkeypatch):
    def unresolved(book_id):
        raise AssertionError("series_index must be validated before the network")

    monkeypatch.setattr(server, "_resolve_book_ids", unresolved)

    with pytest.raises(ValueError, match="series_index must be zero or greater"):
        server.series_books("1", series_index=-1)


# ------------------------------------------------------------ popular_books


def test_popular_books_skips_edges_without_a_node(monkeypatch):
    """Top-list pages carry null/placeholder edges; they must not become entries."""
    recorder = _Recorder(
        [
            {
                "getTopList": {
                    "edges": [
                        None,
                        {"rank": 1, "count": 10, "node": None},
                        {
                            "rank": 2,
                            "count": 9,
                            "node": {"legacyId": 7, "title": "Kept"},
                        },
                    ],
                    "pageInfo": {"hasNextPage": False, "nextPageToken": None},
                }
            }
        ]
    )
    monkeypatch.setattr(server.gr, "graphql", recorder)

    result = server.popular_books(2024, limit=20)

    assert result["returned"] == 1
    assert result["books"] == [
        {
            "rank": 2,
            "count": 9,
            "book_id": 7,
            "title": "Kept",
            "author": None,
            "average_rating": None,
            "ratings_count": None,
            "cover": None,
            "url": None,
        }
    ]


def test_popular_books_stops_when_the_page_says_there_is_no_next(monkeypatch):
    recorder = _Recorder(
        [
            {
                "getTopList": {
                    "edges": [{"rank": 1, "count": 3, "node": {"legacyId": 1}}],
                    "pageInfo": {"hasNextPage": False, "nextPageToken": "page-2"},
                }
            }
        ]
    )
    monkeypatch.setattr(server.gr, "graphql", recorder)

    result = server.popular_books(2024, limit=20)

    assert result["returned"] == 1
    assert len(recorder.calls) == 1, "hasNextPage=False must end the loop"


def test_popular_books_stops_when_a_page_is_empty(monkeypatch):
    recorder = _Recorder(
        [
            {
                "getTopList": {
                    "edges": [],
                    "pageInfo": {"hasNextPage": True, "nextPageToken": "page-2"},
                }
            }
        ]
    )
    monkeypatch.setattr(server.gr, "graphql", recorder)

    result = server.popular_books(2024, month=3, limit=20)

    assert result["returned"] == 0
    assert result["month"] == 3
    assert len(recorder.calls) == 1, "an empty page must end the loop"


# ------------------------------------------------------------ compare_books


def test_compare_books_rejects_an_empty_id_list(monkeypatch):
    monkeypatch.setattr(server, "get_book", _forbidden_graphql)

    with pytest.raises(ValueError, match="at least one book_id"):
        server.compare_books([])
