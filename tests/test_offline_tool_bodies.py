"""Offline tests for the tool bodies only the live suite executes.

Every assertion here is about what a tool *assembles* — the request it sends,
the fields it copies out of a response, the arithmetic it does, the order it
returns things in. That work is deterministic, but until now the only tests
that ran it were the live ones under ``tests/e2e``, which are skipped unless
``GOODREADS_LIVE=1``. The offline suite CI gates on executed none of these
bodies, so a mis-copied field or an inverted sort shipped green.

These tests stay offline: ``gr.get`` and ``gr.graphql`` are replaced with
fakes that answer from fixtures, so the response *shapes* still come from the
live suite's territory while the assembly logic is checked here. The last
section does the same for the two client lines every tool leans on -- the lazy
transport and the GraphQL-config cache -- which no offline test reached either.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from goodreads_mcp import server
from goodreads_mcp.client import BASE, GoodreadsClient


class _Response:
    """Minimal stand-in for the httpx response ``gr.get`` returns."""

    def __init__(self, payload: Any = None, text: str = ""):
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class _Get:
    """Stand-in for ``server.gr.get`` that records every call."""

    def __init__(self, response: _Response):
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kw) -> _Response:
        self.calls.append((url, kw.get("params") or {}))
        return self.response


class _Graphql:
    """Stand-in for ``server.gr.graphql`` that answers per query document.

    Keying on the query constant (rather than a fixed response order) lets the
    real ``_resolve_book_ids`` and ``_paginated_graphql_edges`` run, so these
    tests cover the tool body *and* the wiring between it and the helpers.
    """

    def __init__(self, pages: dict[str, list[dict[str, Any]]]):
        self.pages = {q: list(v) for q, v in pages.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((query, variables))
        queued = self.pages.get(query)
        if not queued:
            raise AssertionError(f"unexpected graphql call for query: {query[:40]!r}")
        return queued.pop(0)

    def variables_for(self, query: str) -> list[dict[str, Any]]:
        return [v for q, v in self.calls if q == query]


_BOOK_IDS_RESPONSE = {
    "getBookByLegacyId": {
        "id": "kca://book/1",
        "legacyId": 1,
        "title": "A Book",
        "titleComplete": "A Book: Complete",
        "work": {"id": "kca://work/1"},
        "primaryContributorEdge": {
            "node": {
                "id": "kca://author/9",
                "name": "An Author",
                "webUrl": "https://www.goodreads.com/author/show/9",
            }
        },
        "bookSeries": [
            {"userPosition": "1", "series": {"id": "kca://series/5", "title": "Arc"}}
        ],
    }
}


def _book_node(legacy_id: int, title: str, rating: float) -> dict[str, Any]:
    return {
        "legacyId": legacy_id,
        "title": title,
        "imageUrl": f"https://images.example/{legacy_id}.jpg",
        "webUrl": f"https://www.goodreads.com/book/show/{legacy_id}",
        "primaryContributorEdge": {"node": {"name": f"Author {legacy_id}"}},
        "work": {"stats": {"averageRating": rating, "ratingsCount": legacy_id * 10}},
    }


def _work_node(legacy_id: int, title: str, rating: float) -> dict[str, Any]:
    return {
        "bestBook": {
            "legacyId": legacy_id,
            "title": title,
            "imageUrl": f"https://images.example/{legacy_id}.jpg",
            "webUrl": f"https://www.goodreads.com/book/show/{legacy_id}",
            "primaryContributorEdge": {"node": {"name": f"Author {legacy_id}"}},
        },
        "stats": {"averageRating": rating, "ratingsCount": legacy_id * 10},
    }


def _page(edges: list[dict[str, Any]], **page_info: Any) -> dict[str, Any]:
    info = {"hasNextPage": False, "nextPageToken": None}
    total = page_info.pop("totalCount", None)
    info.update(page_info)
    page: dict[str, Any] = {"edges": edges, "pageInfo": info}
    if total is not None:
        page["totalCount"] = total
    return page


# ------------------------------------------------------------- search_books


def test_search_books_normalizes_the_autocomplete_payload(monkeypatch):
    payload = [
        {
            "bookId": "54493401",
            "title": "Project Hail Mary",
            "author": {"name": "Andy Weir"},
            "avgRating": "4.51",
            "ratingsCount": 1234,
            "numPages": 476,
            "imageUrl": "https://images.example/phm.jpg",
            "bookUrl": "/book/show/54493401-project-hail-mary",
            "description": {"html": "A <b>lone</b> astronaut &amp; a problem."},
        }
    ]
    get = _Get(_Response(payload))
    monkeypatch.setattr(server.gr, "get", get)

    results = server.search_books("project hail mary")

    assert get.calls == [
        ("/book/auto_complete", {"format": "json", "q": "project hail mary"})
    ]
    assert results == [
        {
            "book_id": "54493401",
            "title": "Project Hail Mary",
            "author": "Andy Weir",
            "average_rating": "4.51",
            "ratings_count": 1234,
            "pages": 476,
            "cover": "https://images.example/phm.jpg",
            "url": server.BASE + "/book/show/54493401-project-hail-mary",
            "description": "A lone astronaut & a problem.",
        }
    ]


def test_search_books_truncates_to_max_results(monkeypatch):
    payload = [{"bookId": str(i), "title": f"Book {i}"} for i in range(5)]
    monkeypatch.setattr(server.gr, "get", _Get(_Response(payload)))

    results = server.search_books("anything", max_results=2)

    assert [r["book_id"] for r in results] == ["0", "1"]


def test_search_books_tolerates_a_result_missing_every_optional_field(monkeypatch):
    monkeypatch.setattr(server.gr, "get", _Get(_Response([{"bookId": "7"}])))

    (result,) = server.search_books("anything")

    assert result["author"] is None
    assert result["description"] == ""
    # A missing bookUrl must not produce a link to some other page (BASE alone
    # is the Goodreads home page); SERVER_INSTRUCTIONS says null is explicit.
    assert result["url"] is None


def test_search_books_returns_everything_when_max_results_exceeds_the_payload(
    monkeypatch,
):
    """The autocomplete endpoint answers ~5 matches; a larger ask is not an error."""
    payload = [{"bookId": str(i)} for i in range(5)]
    monkeypatch.setattr(server.gr, "get", _Get(_Response(payload)))

    results = server.search_books("anything", max_results=10)

    assert [r["book_id"] for r in results] == ["0", "1", "2", "3", "4"]


def test_search_books_returns_nothing_for_zero_max_results(monkeypatch):
    get = _Get(_Response([{"bookId": "1"}]))
    monkeypatch.setattr(server.gr, "get", get)

    assert server.search_books("anything", max_results=0) == []


def test_search_books_rejects_a_negative_max_results(monkeypatch):
    """A negative slice bound drops the tail silently: [:-1] returned 4 of 5."""
    get = _Get(_Response([{"bookId": str(i)} for i in range(5)]))
    monkeypatch.setattr(server.gr, "get", get)

    with pytest.raises(ValueError, match="max_results must be zero or greater"):
        server.search_books("anything", max_results=-1)

    assert get.calls == []  # refused before any request went out


def test_search_books_caps_the_description_at_400_characters(monkeypatch):
    long_html = "<p>" + ("x" * 500) + "</p>"
    payload = [{"bookId": "7", "description": {"html": long_html}}]
    monkeypatch.setattr(server.gr, "get", _Get(_Response(payload)))

    (result,) = server.search_books("anything")

    assert result["description"] == "x" * 400


# --------------------------------------------------- get_book page fetching


def test_fetch_book_apollo_uses_the_xml_path_that_dodges_the_waf(monkeypatch):
    """The plain HTML page is WAF-gated; only the .xml suffix is served."""
    next_data = {"props": {"pageProps": {"apolloState": {"Book:1": {"title": "A"}}}}}
    html = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(next_data)
        + "</script></html>"
    )
    get = _Get(_Response(text=html))
    monkeypatch.setattr(server.gr, "get", get)

    apollo = server._fetch_book_apollo("54493401")

    assert get.calls == [("/book/show/54493401.xml", {})]
    assert apollo == {"Book:1": {"title": "A"}}


def test_fetch_book_apollo_does_not_double_suffix_an_xml_id(monkeypatch):
    next_data = {"props": {"pageProps": {"apolloState": {}}}}
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(next_data)
        + "</script>"
    )
    get = _Get(_Response(text=html))
    monkeypatch.setattr(server.gr, "get", get)

    server._fetch_book_apollo("54493401.xml")

    assert get.calls == [("/book/show/54493401.xml", {})]


def test_get_book_follows_apollo_references(monkeypatch):
    """Apollo stores nested objects as ``__ref`` pointers, not inline values."""
    apollo = {
        "Book:1": {
            "legacyId": 1,
            "title": "A Book",
            "primaryContributorEdge": {"__ref": "ContributorEdge:1"},
            "work": {"__ref": "Work:1"},
            "bookGenres": [{"genre": {"__ref": "Genre:sf"}}],
            "bookSeries": [{"userPosition": "1", "series": {"__ref": "Series:5"}}],
        },
        "ContributorEdge:1": {"node": {"__ref": "Contributor:9"}},
        "Contributor:9": {"name": "An Author"},
        "Work:1": {"stats": {"averageRating": 4.2, "ratingsCount": 11}},
        "Genre:sf": {"name": "Science Fiction"},
        "Series:5": {"title": "Arc"},
    }
    monkeypatch.setattr(server, "_fetch_book_apollo", lambda book_id: apollo)

    book = server.get_book("1")

    assert book["author"] == "An Author"
    assert book["average_rating"] == 4.2
    assert book["genres"] == ["Science Fiction"]
    assert book["series"] == "Arc"


# -------------------------------------------------------------- get_reviews


def _reviews_book_response() -> dict[str, Any]:
    return {
        "getBookByLegacyId": {
            "legacyId": 1,
            "title": "A Book",
            "work": {"id": "kca://work/1"},
        }
    }


def _review_edge(name: str, rating: int, spoiler: bool = False) -> dict[str, Any]:
    return {
        "node": {
            "rating": rating,
            "text": f"<p>{name} liked it.</p>",
            "spoilerStatus": spoiler,
            "creator": {
                "name": name,
                "webUrl": f"https://www.goodreads.com/user/show/{name}",
            },
            "shelving": {"webUrl": f"https://www.goodreads.com/review/{name}"},
        }
    }


def test_get_reviews_sends_both_star_filters(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_BY_LEGACY: [_reviews_book_response()],
            server._Q_REVIEWS: [{"getReviews": _page([])}],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    server.get_reviews("1", limit=5, min_rating=4, max_rating=5)

    (variables,) = graphql.variables_for(server._Q_REVIEWS)
    assert variables["filters"] == {
        "resourceType": "WORK",
        "resourceId": "kca://work/1",
        "ratingMin": 4,
        "ratingMax": 5,
    }


def test_get_reviews_omits_star_filters_that_were_not_asked_for(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_BY_LEGACY: [_reviews_book_response()],
            server._Q_REVIEWS: [{"getReviews": _page([])}],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    server.get_reviews("1", limit=5)

    (variables,) = graphql.variables_for(server._Q_REVIEWS)
    assert "ratingMin" not in variables["filters"]
    assert "ratingMax" not in variables["filters"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_rating": 0}, "min_rating must be between 1 and 5"),
        ({"min_rating": 6}, "min_rating must be between 1 and 5"),
        ({"max_rating": 0}, "max_rating must be between 1 and 5"),
        ({"max_rating": 6}, "max_rating must be between 1 and 5"),
        (
            {"min_rating": 4, "max_rating": 2},
            "min_rating must not be greater than max_rating",
        ),
    ],
)
def test_get_reviews_rejects_a_star_filter_outside_one_to_five(
    kwargs, message, monkeypatch
):
    """Goodreads answers an impossible filter with totalCount null and no
    edges, which the tool would report as returned=0 -- the same shape as a
    book with no reviews. The filter is refused before any request is made."""
    graphql = _Graphql({})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    with pytest.raises(ValueError, match=message):
        server.get_reviews("1", **kwargs)

    assert graphql.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_rating": 1},
        {"max_rating": 5},
        {"min_rating": 1, "max_rating": 5},
        {"min_rating": 3, "max_rating": 3},
    ],
)
def test_get_reviews_accepts_the_edges_of_the_star_range(kwargs, monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_BY_LEGACY: [_reviews_book_response()],
            server._Q_REVIEWS: [{"getReviews": _page([])}],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    server.get_reviews("1", limit=5, **kwargs)

    (variables,) = graphql.variables_for(server._Q_REVIEWS)
    for name, key in (("min_rating", "ratingMin"), ("max_rating", "ratingMax")):
        if name in kwargs:
            assert variables["filters"][key] == kwargs[name]


def test_get_reviews_pages_with_the_next_token(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_BY_LEGACY: [_reviews_book_response()],
            server._Q_REVIEWS: [
                {
                    "getReviews": _page(
                        [_review_edge("ann", 5)],
                        totalCount=2,
                        hasNextPage=True,
                        nextPageToken="page-2",
                    )
                },
                {"getReviews": _page([_review_edge("bob", 3)])},
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.get_reviews("1", limit=10)

    first, second = graphql.variables_for(server._Q_REVIEWS)
    assert "after" not in first["pagination"]
    assert second["pagination"]["after"] == "page-2"
    assert [r["reviewer"] for r in result["reviews"]] == ["ann", "bob"]
    assert result["total_text_reviews"] == 2
    assert result["returned"] == 2
    assert result["title"] == "A Book"


def test_get_reviews_drops_spoilers_when_asked(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_BY_LEGACY: [_reviews_book_response()],
            server._Q_REVIEWS: [
                {
                    "getReviews": _page(
                        [
                            _review_edge("ann", 5, spoiler=True),
                            _review_edge("bob", 4),
                        ]
                    )
                }
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.get_reviews("1", limit=10, exclude_spoilers=True)

    assert [r["reviewer"] for r in result["reviews"]] == ["bob"]
    assert result["returned"] == 1


def test_get_reviews_keeps_spoilers_by_default_and_flags_them(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_BY_LEGACY: [_reviews_book_response()],
            server._Q_REVIEWS: [
                {"getReviews": _page([_review_edge("ann", 5, spoiler=True)])}
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    (review,) = server.get_reviews("1", limit=10)["reviews"]

    assert review["spoiler"] is True
    assert review["text"] == "ann liked it."
    assert review["url"] == "https://www.goodreads.com/review/ann"
    assert review["reviewer_url"] == "https://www.goodreads.com/user/show/ann"


def test_get_reviews_stops_mid_page_once_the_limit_is_reached(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_BY_LEGACY: [_reviews_book_response()],
            server._Q_REVIEWS: [
                {
                    "getReviews": _page(
                        [_review_edge("ann", 5), _review_edge("bob", 4)],
                        hasNextPage=True,
                        nextPageToken="page-2",
                    )
                }
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.get_reviews("1", limit=1)

    assert [r["reviewer"] for r in result["reviews"]] == ["ann"]
    assert len(graphql.variables_for(server._Q_REVIEWS)) == 1


# ------------------------------------------------------------ similar_books


def test_similar_books_summarizes_book_nodes(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_IDS: [_BOOK_IDS_RESPONSE],
            server._Q_SIMILAR: [
                {
                    "getSimilarBooks": _page(
                        [{"node": _book_node(2, "Another Book", 4.3)}],
                        hasNextPage=True,
                        nextPageToken="page-2",
                    )
                }
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.similar_books("1-a-book", limit=1)

    (variables,) = graphql.variables_for(server._Q_SIMILAR)
    assert variables["id"] == "kca://book/1"
    assert result["book_id"] == 1
    assert result["title"] == "A Book: Complete"
    assert result["returned"] == 1
    assert result["has_more"] is True
    assert result["similar"] == [
        {
            "book_id": 2,
            "title": "Another Book",
            "author": "Author 2",
            "average_rating": 4.3,
            "ratings_count": 20,
            "cover": "https://images.example/2.jpg",
            "url": "https://www.goodreads.com/book/show/2",
        }
    ]


def test_similar_books_rejects_a_negative_limit(monkeypatch):
    monkeypatch.setattr(server.gr, "graphql", _Graphql({}))

    with pytest.raises(ValueError, match="zero or greater"):
        server.similar_books("1", limit=-1)


# ------------------------------------------------------------- author_books


def test_author_books_reports_the_contributor_and_total(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_IDS: [_BOOK_IDS_RESPONSE],
            server._Q_AUTHOR: [
                {
                    "getWorksByContributor": _page(
                        [{"node": _work_node(3, "Earlier Work", 3.9)}],
                        totalCount=42,
                    )
                }
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.author_books("1", limit=1)

    (variables,) = graphql.variables_for(server._Q_AUTHOR)
    assert variables["input"] == {"id": "kca://author/9"}
    assert result["author"] == "An Author"
    assert result["author_url"] == "https://www.goodreads.com/author/show/9"
    assert result["total_works"] == 42
    assert result["returned"] == 1
    # 1 of 42 returned: more remain even though the page said hasNextPage=False.
    assert result["has_more"] is True
    assert result["works"] == [
        {
            "book_id": 3,
            "title": "Earlier Work",
            "author": "Author 3",
            "average_rating": 3.9,
            "ratings_count": 30,
            "cover": "https://images.example/3.jpg",
            "url": "https://www.goodreads.com/book/show/3",
        }
    ]


# ------------------------------------------------------------- series_books


def test_series_books_returns_reading_order_placements(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_IDS: [_BOOK_IDS_RESPONSE],
            server._Q_SERIES: [
                {
                    "getWorksForSeries": _page(
                        [
                            {
                                "node": _work_node(4, "Prequel", 3.5),
                                "seriesPlacement": "0.5",
                                "isPrimary": False,
                            },
                            {
                                "node": _work_node(5, "Book One", 4.4),
                                "seriesPlacement": "1",
                                "isPrimary": True,
                            },
                        ]
                    )
                }
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.series_books("1", limit=2)

    (variables,) = graphql.variables_for(server._Q_SERIES)
    assert variables["input"] == {"id": "kca://series/5"}
    # The input book is echoed like every other discovery tool does (#95).
    assert result["book_id"] == 1
    assert result["title"] == "A Book: Complete"
    assert result["series"] == "Arc"
    assert result["series_index"] == 0
    assert result["returned"] == 2
    assert result["has_more"] is False
    assert [(b["title"], b["placement"], b["is_primary"]) for b in result["books"]] == [
        ("Prequel", "0.5", False),
        ("Book One", "1", True),
    ]
    assert set(result) == {
        "book_id",
        "title",
        "series",
        "series_index",
        "returned",
        "has_more",
        "books",
    }


def test_series_books_notes_a_standalone_without_a_second_call(monkeypatch):
    standalone = {
        "getBookByLegacyId": {
            "id": "kca://book/1",
            "legacyId": 1,
            "title": "A Standalone",
            "work": {"id": "kca://work/1"},
            "bookSeries": [],
        }
    }
    graphql = _Graphql({server._Q_BOOK_IDS: [standalone]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.series_books("1")

    assert result["book_id"] == 1
    assert result["title"] == "A Standalone"
    assert result["series"] is None
    assert result["note"] == "This book isn't part of a Goodreads series."
    assert result["books"] == []
    assert result["returned"] == 0
    assert result["has_more"] is False
    assert len(graphql.calls) == 1
    # Both branches share the chainable keys; only this one adds `note`.
    assert set(result) == {
        "book_id",
        "title",
        "series",
        "note",
        "returned",
        "has_more",
        "books",
    }


# ------------------------------------------------------------- get_editions


def test_get_editions_flattens_the_edition_details(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_IDS: [_BOOK_IDS_RESPONSE],
            server._Q_EDITIONS: [
                {
                    "getEditions": _page(
                        [
                            {
                                "node": {
                                    "legacyId": 11,
                                    "title": "A Book (Hardcover)",
                                    "imageUrl": "https://images.example/11.jpg",
                                    "webUrl": "https://www.goodreads.com/book/show/11",
                                    "details": {
                                        "format": "Hardcover",
                                        "publisher": "A Press",
                                        "publicationTime": 1600396415413,
                                        "isbn13": "9780000000001",
                                        "numPages": 320,
                                        "language": {"name": "English"},
                                    },
                                }
                            }
                        ],
                        totalCount=1,
                    )
                }
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.get_editions("1", limit=1)

    (variables,) = graphql.variables_for(server._Q_EDITIONS)
    assert variables["id"] == "kca://work/1"
    assert result["book_id"] == 1
    assert result["total_editions"] == 1
    assert result["returned"] == 1
    assert result["editions"] == [
        {
            "book_id": 11,
            "title": "A Book (Hardcover)",
            "cover": "https://images.example/11.jpg",
            "format": "Hardcover",
            "publisher": "A Press",
            "publication_time": 1600396415413,
            "isbn13": "9780000000001",
            "pages": 320,
            "language": "English",
            "url": "https://www.goodreads.com/book/show/11",
        }
    ]


def test_get_editions_tolerates_an_edition_without_details(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_IDS: [_BOOK_IDS_RESPONSE],
            server._Q_EDITIONS: [
                {"getEditions": _page([{"node": {"legacyId": 12, "title": "Bare"}}])}
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    (edition,) = server.get_editions("1", limit=1)["editions"]

    assert edition["book_id"] == 12
    assert edition["format"] is None
    assert edition["language"] is None
    assert edition["isbn13"] is None


# ---------------------------------------------------------------- book_lists


def test_book_lists_summarizes_listopia_entries(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_IDS: [_BOOK_IDS_RESPONSE],
            server._Q_BOOK_LISTS: [
                {
                    "getBookListsOfBook": _page(
                        [
                            {
                                "node": {
                                    "legacyId": 77,
                                    "title": "Best Dystopian Fiction",
                                    "userListVotesCount": 5000,
                                    "listBooksCount": 250,
                                    "webUrl": "https://www.goodreads.com/list/show/77",
                                }
                            }
                        ]
                    )
                }
            ],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.book_lists("1", limit=1)

    (variables,) = graphql.variables_for(server._Q_BOOK_LISTS)
    assert variables["id"] == "kca://book/1"
    assert result["book_id"] == 1
    assert result["returned"] == 1
    assert result["lists"] == [
        {
            "list_id": 77,
            "title": "Best Dystopian Fiction",
            "votes": 5000,
            "books_count": 250,
            "url": "https://www.goodreads.com/list/show/77",
        }
    ]


def test_book_lists_tolerates_an_edge_with_no_node(monkeypatch):
    graphql = _Graphql(
        {
            server._Q_BOOK_IDS: [_BOOK_IDS_RESPONSE],
            server._Q_BOOK_LISTS: [{"getBookListsOfBook": _page([{"node": None}])}],
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    (entry,) = server.book_lists("1", limit=1)["lists"]

    assert entry == {
        "list_id": None,
        "title": None,
        "votes": None,
        "books_count": None,
        "url": None,
    }


# ------------------------------------------------------------ popular_books


def test_popular_books_asks_for_the_whole_year_by_default(monkeypatch):
    graphql = _Graphql({server._Q_TOP_LIST: [{"getTopList": _page([])}]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.popular_books(2024, limit=5)

    (variables,) = graphql.variables_for(server._Q_TOP_LIST)
    assert variables["name"] == "works-by-release-date-2024"
    assert variables["after"] is None
    assert variables["limit"] == server._POPULAR_PAGE_SIZE
    assert result == {
        "year": 2024,
        "month": None,
        "returned": 0,
        "has_more": False,
        "books": [],
    }


def test_popular_books_asks_for_a_single_month_when_given_one(monkeypatch):
    graphql = _Graphql({server._Q_TOP_LIST: [{"getTopList": _page([])}]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.popular_books(2024, month=3, limit=5)

    (variables,) = graphql.variables_for(server._Q_TOP_LIST)
    assert variables["name"] == "books-by-release-date-2024-3"
    assert result["month"] == 3


@pytest.mark.parametrize("month", [0, 13, -1])
def test_popular_books_rejects_a_month_outside_the_calendar(month, monkeypatch):
    graphql = _Graphql({})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    with pytest.raises(ValueError, match="month must be between 1 and 12"):
        server.popular_books(2024, month=month)

    assert graphql.calls == []


@pytest.mark.parametrize("month", [1, 12])
def test_popular_books_accepts_the_edges_of_the_calendar(month, monkeypatch):
    graphql = _Graphql({server._Q_TOP_LIST: [{"getTopList": _page([])}]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    assert server.popular_books(2024, month=month, limit=1)["month"] == month


def test_popular_books_carries_rank_and_count_onto_the_summary(monkeypatch):
    node = _work_node(6, "A Popular Work", 4.0)
    node["__typename"] = "Work"
    graphql = _Graphql(
        {
            server._Q_TOP_LIST: [
                {"getTopList": _page([{"rank": 1, "count": 9001, "node": node}])}
            ]
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    (entry,) = server.popular_books(2024, limit=5)["books"]

    assert entry["rank"] == 1
    assert entry["count"] == 9001
    assert entry["book_id"] == 6
    assert entry["title"] == "A Popular Work"
    assert entry["author"] == "Author 6"
    assert entry["average_rating"] == 4.0


def test_popular_books_reads_a_work_node_nested_under_details(monkeypatch):
    """The top-list shape puts the representative book at details.bestBook."""
    node = {
        "__typename": "Work",
        "details": {"bestBook": {"legacyId": 8, "title": "Nested Best Book"}},
        "stats": {"averageRating": 4.8},
    }
    graphql = _Graphql(
        {
            server._Q_TOP_LIST: [
                {"getTopList": _page([{"rank": 2, "count": 5, "node": node}])}
            ]
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    (entry,) = server.popular_books(2024, limit=5)["books"]

    assert entry["book_id"] == 8
    assert entry["title"] == "Nested Best Book"
    assert entry["average_rating"] == 4.8


def test_popular_books_follows_the_next_page_token(monkeypatch):
    first = _page(
        [{"rank": 1, "count": 3, "node": _book_node(1, "One", 4.0)}],
        hasNextPage=True,
        nextPageToken="page-2",
    )
    second = _page([{"rank": 2, "count": 2, "node": _book_node(2, "Two", 3.0)}])
    graphql = _Graphql(
        {server._Q_TOP_LIST: [{"getTopList": first}, {"getTopList": second}]}
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.popular_books(2024, limit=5)

    calls = graphql.variables_for(server._Q_TOP_LIST)
    assert [c["after"] for c in calls] == [None, "page-2"]
    assert [b["rank"] for b in result["books"]] == [1, 2]
    assert result["returned"] == 2
    # The second page said hasNextPage=False, so the chart is exhausted.
    assert result["has_more"] is False
    assert set(result) == {"year", "month", "returned", "has_more", "books"}


def test_popular_books_reports_more_when_the_limit_lands_on_a_page_boundary(
    monkeypatch,
):
    """Stopped at `limit` with the page still promising a next page (#95)."""
    only = _page(
        [{"rank": 1, "count": 3, "node": _book_node(1, "One", 4.0)}],
        hasNextPage=True,
        nextPageToken="page-2",
    )
    graphql = _Graphql({server._Q_TOP_LIST: [{"getTopList": only}]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.popular_books(2024, limit=1)

    assert result["returned"] == 1
    assert result["has_more"] is True
    assert len(graphql.calls) == 1, "must not fetch a page it will discard"


def test_popular_books_reports_more_when_the_limit_stops_mid_page(monkeypatch):
    """Entries left unread on the page count as more, even with no next page."""
    only = _page(
        [
            {"rank": 1, "count": 3, "node": _book_node(1, "One", 4.0)},
            {"rank": 2, "count": 2, "node": _book_node(2, "Two", 3.0)},
        ]
    )
    graphql = _Graphql({server._Q_TOP_LIST: [{"getTopList": only}]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.popular_books(2024, limit=1)

    assert [b["rank"] for b in result["books"]] == [1]
    assert result["has_more"] is True


def test_popular_books_reports_no_more_when_the_page_ends_the_chart(monkeypatch):
    only = _page(
        [{"rank": 1, "count": 3, "node": _book_node(1, "One", 4.0)}],
        hasNextPage=False,
        nextPageToken="page-2",
    )
    graphql = _Graphql({server._Q_TOP_LIST: [{"getTopList": only}]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.popular_books(2024, limit=20)

    assert result["returned"] == 1
    assert result["has_more"] is False


def test_popular_books_caps_the_request_at_the_maximum(monkeypatch):
    def full_page(offset: int, **info: Any) -> dict[str, Any]:
        edges = [
            {"rank": offset + i, "count": 1, "node": _book_node(offset + i, "x", 4.0)}
            for i in range(server._POPULAR_PAGE_SIZE)
        ]
        return {"getTopList": _page(edges, **info)}

    graphql = _Graphql(
        {
            server._Q_TOP_LIST: [
                full_page(1, hasNextPage=True, nextPageToken="page-2"),
                full_page(31, hasNextPage=True, nextPageToken="page-3"),
            ]
        }
    )
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.popular_books(2024, limit=999)

    assert result["returned"] == server._MAX_POPULAR
    # Stopped mid-page rather than draining a third page for entries to discard.
    assert len(graphql.variables_for(server._Q_TOP_LIST)) == 2
    # ...and says so: the cap, not the chart, ended the listing.
    assert result["has_more"] is True


# ------------------------------------------------------------ compare_books


def _compared(book_id: str, rating: float | None, hist: dict[str, int] | None):
    return {
        "book_id": int(book_id),
        "title": f"Book {book_id}",
        "author": f"Author {book_id}",
        "average_rating": rating,
        "ratings_count": 100,
        "text_reviews_count": 10,
        "ratings_histogram": hist,
        "url": f"https://www.goodreads.com/book/show/{book_id}",
    }


def test_compare_books_ranks_by_rating_and_computes_polarization(monkeypatch):
    books = {
        "1": _compared("1", 3.0, {"1": 1, "2": 1, "3": 0, "4": 4, "5": 4}),
        "2": _compared("2", 4.5, {"1": 0, "2": 0, "3": 0, "4": 1, "5": 1}),
    }
    monkeypatch.setattr(server, "get_book", lambda bid: books[bid])

    result = server.compare_books(["1", "2"])

    assert result["compared"] == 2
    assert result["ranked_by"] == "average_rating (desc)"
    assert [b["book_id"] for b in result["books"]] == [2, 1]
    low, = [b for b in result["books"] if b["book_id"] == 1]
    assert low["pct_positive"] == 80.0
    assert low["pct_critical"] == 20.0
    high, = [b for b in result["books"] if b["book_id"] == 2]
    assert high["pct_positive"] == 100.0
    assert high["pct_critical"] == 0.0


def test_compare_books_reports_no_percentages_without_a_histogram(monkeypatch):
    monkeypatch.setattr(server, "get_book", lambda bid: _compared(bid, 4.0, None))

    (book,) = server.compare_books(["1"])["books"]

    assert book["ratings_histogram"] is None
    assert book["pct_positive"] is None
    assert book["pct_critical"] is None


def test_compare_books_puts_unrated_then_failed_books_last(monkeypatch):
    def fake_get_book(bid: str) -> dict[str, Any]:
        if bid == "3":
            raise ValueError(f"No book found for id {bid!r}.")
        return _compared(bid, None if bid == "2" else 4.0, None)

    monkeypatch.setattr(server, "get_book", fake_get_book)

    result = server.compare_books(["1", "2", "3"])

    assert result["compared"] == 2  # the failed id is not counted as compared
    assert [b.get("book_id") for b in result["books"]] == [1, 2, "3"]
    assert result["books"][-1]["error"] == "No book found for id '3'."


def test_compare_books_compares_exactly_the_maximum_number_of_ids(monkeypatch):
    fetched: list[str] = []

    def fake_get_book(bid: str) -> dict[str, Any]:
        fetched.append(bid)
        return _compared(bid, 4.0, None)

    monkeypatch.setattr(server, "get_book", fake_get_book)

    ids = [str(i) for i in range(1, server._MAX_COMPARE + 1)]
    result = server.compare_books(ids)

    assert fetched == ids
    assert result["compared"] == server._MAX_COMPARE


def test_compare_books_refuses_more_ids_than_the_fan_out_cap(monkeypatch):
    """Trimming to the cap used to drop the extras without a word, and
    ``compared`` counted only the survivors. Now the call is refused before
    any book is fetched, and the message says how many were passed."""
    fetched: list[str] = []

    def fake_get_book(bid: str) -> dict[str, Any]:
        fetched.append(bid)
        return _compared(bid, 4.0, None)

    monkeypatch.setattr(server, "get_book", fake_get_book)

    ids = [str(i) for i in range(1, server._MAX_COMPARE + 4)]
    with pytest.raises(ValueError, match=r"at most 10 book ids; got 13"):
        server.compare_books(ids)

    assert fetched == []


# ---------------------------------------------------------------- get_shelf


def test_get_shelf_requests_the_rss_feed_and_returns_its_parse(monkeypatch):
    parsed = [{"title": "A Book", "link": "https://www.goodreads.com/book/show/1"}]
    get = _Get(_Response(text="<rss/>"))
    monkeypatch.setattr(server.gr, "get", get)
    monkeypatch.setattr(server.gr, "parse_shelf_rss", lambda text: parsed)
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "111")

    result = server.get_shelf()

    assert get.calls == [("/review/list_rss/111", {"shelf": "to-read", "page": 1})]
    assert result is parsed


def test_get_shelf_passes_the_requested_shelf_user_and_page(monkeypatch):
    get = _Get(_Response(text="<rss/>"))
    monkeypatch.setattr(server.gr, "get", get)
    monkeypatch.setattr(server.gr, "parse_shelf_rss", lambda text: [])

    server.get_shelf(shelf="read", user_id="222", page=3)

    assert get.calls == [("/review/list_rss/222", {"shelf": "read", "page": 3})]


@pytest.mark.parametrize("page", [0, -1])
def test_get_shelf_rejects_a_page_below_one(page, monkeypatch):
    """page=0 or a negative page went straight into the RSS URL."""
    get = _Get(_Response(text="<rss/>"))
    monkeypatch.setattr(server.gr, "get", get)
    monkeypatch.setattr(server.gr, "parse_shelf_rss", lambda text: [])

    with pytest.raises(ValueError, match="page must be 1 or greater"):
        server.get_shelf(user_id="222", page=page)

    assert get.calls == []


def test_get_shelf_parses_the_body_of_the_response_it_fetched(monkeypatch):
    """Guard against parsing a stale/empty string instead of the fetched feed."""
    seen: list[str] = []
    monkeypatch.setattr(server.gr, "get", _Get(_Response(text="<rss>feed</rss>")))
    monkeypatch.setattr(
        server.gr, "parse_shelf_rss", lambda text: seen.append(text) or []
    )

    server.get_shelf(user_id="222")

    assert seen == ["<rss>feed</rss>"]


# ------------------------------------------- client state every tool leans on


def test_client_builds_its_transport_once_and_points_it_at_goodreads():
    """Every tool goes through this property; a per-call client would drop
    connection reuse and the shared headers silently."""
    client = GoodreadsClient()
    assert client._client is None

    transport = client.client
    try:
        assert str(transport.base_url) == BASE
        # A second access must hand back the same client, not a fresh one.
        assert client.client is transport
    finally:
        transport.close()


def test_graphql_config_is_discovered_once_and_then_cached():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.startswith("/_next/"):
            return httpx.Response(
                200,
                text=(
                    '{"Prod":{"appsync":{"apiKey":"da2-legacykey00000000000000",'
                    '"endpoint":"https://kxbwmqov6jgg3daaamb744ycu4.appsync-api.'
                    'us-east-1.amazonaws.com/graphql","region":"us-east-1"},'
                    '"shortName":"Prod"}}'
                ),
            )
        return httpx.Response(
            200,
            text='<script src="/_next/static/chunks/pages/_app-deadbeef.js"></script>',
        )

    client = GoodreadsClient()
    client._client = httpx.Client(
        base_url=BASE, transport=httpx.MockTransport(handler)
    )
    try:
        first = client.graphql_config()
        after_discovery = list(requests)
        second = client.graphql_config()

        assert second == first
        # The cache hit must not re-fetch the page or the bundle.
        assert requests == after_discovery
        # force=True is the escape hatch when the cached pair stops working.
        assert client.graphql_config(force=True) == first
        assert requests == after_discovery + after_discovery
    finally:
        client._client.close()
