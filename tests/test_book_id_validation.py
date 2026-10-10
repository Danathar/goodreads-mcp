"""Offline tests for the book_id and user_id checks in front of every request.

Before #205 a book_id only had to *start* with digits, so an ISBN such as
``978-0-593-13520-4`` was read as book 978 plus a slug and answered with an
unrelated book, and whatever followed the digits (``?query``, ``#fragment``,
``../``) went into get_book's URL path. Each case here is a value a model can
plausibly pass; a refused one must fail before any request is sent.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from goodreads_mcp import server

# (book_id as passed, the path segment get_book requests, the legacy id)
_ACCEPTED = [
    ("54493401", "54493401", 54493401),
    ("11870085-the-fault-in-our-stars", "11870085-the-fault-in-our-stars", 11870085),
    ("2767052.The_Hunger_Games", "2767052.The_Hunger_Games", 2767052),
    ("3.Harry_Potter_and_the_Sorcerer_s_Stone", "3.Harry_Potter_and_the_Sorcerer_s_Stone", 3),
    ("  54493401-project-hail-mary\n", "54493401-project-hail-mary", 54493401),
    ("2147483647", "2147483647", 2**31 - 1),
    # Ten characters that pass the ISBN-10 checksum once the hyphen is gone,
    # but an id with a one-character slug is not an ISBN.
    ("100000000-1", "100000000-1", 100000000),
    ("123456789-x", "123456789-x", 123456789),
]

_ISBNS = [
    "978-0-593-13520-4",  # Project Hail Mary; read as book 978 before #205
    "979-8-88645-010-1",
    "9780593135204",
    "159017416X",  # read as book 159017416 before #205
    "0-14-303943-1",
    " 978 0 593 13520 4 ",
]

_MALFORMED = [
    "",
    "   ",
    "054493401",
    "0",
    "-54493401",
    "project-hail-mary",
    "https://www.goodreads.com/book/show/54493401-project-hail-mary",
    "54493401-project-hail-mary?from_search=true&rank=1",
    "54493401-project-hail-mary#CommunityReviews",
    "../../review/list_rss/1",
    "54493401/reviews",
    "54493401-project%2Fhail",
    "54493401 project hail mary",
    "54493401_project",
    "2147483648-a",  # one past GraphQL's 32-bit Int (bare, it is a valid ISBN-10)
    "2147483649",  # past GraphQL's 32-bit Int
    "9780593135205",  # an ISBN-13 with a bad check digit: too big for a book id
]

_GRAPHQL_TOOLS = [
    server.get_reviews,
    server.similar_books,
    server.author_books,
    server.series_books,
    server.get_editions,
    server.book_lists,
]


def _book_page(apollo: dict[str, Any]) -> str:
    next_data = {"props": {"pageProps": {"apolloState": apollo}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(next_data)
        + "</script>"
    )


@pytest.fixture
def requests(monkeypatch) -> list[str]:
    """Record every request; book pages answer with a minimal book."""
    sent: list[str] = []

    def get(url: str, **kw) -> httpx.Response:
        sent.append(url)
        return httpx.Response(200, text=_book_page({"Book:1": {"title": "A"}}))

    def graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        sent.append(f"graphql {variables}")
        return {}

    monkeypatch.setattr(server.gr, "get", get)
    monkeypatch.setattr(server.gr, "graphql", graphql)
    return sent


# ------------------------------------------------------------------ accepted


@pytest.mark.parametrize(("book_id", "segment", "number"), _ACCEPTED)
def test_get_book_requests_the_xml_page_of_the_id_it_was_given(
    requests, book_id, segment, number
):
    assert server.get_book(book_id)["title"] == "A"
    assert requests == [f"/book/show/{segment}.xml"]


@pytest.mark.parametrize(("book_id", "segment", "number"), _ACCEPTED)
def test_legacy_id_is_the_number_of_the_id(book_id, segment, number):
    assert server._legacy_id(book_id) == number


# ------------------------------------------------------------------- refused


@pytest.mark.parametrize("book_id", _ISBNS)
def test_an_isbn_is_refused_and_points_to_search_books(requests, book_id):
    with pytest.raises(ValueError, match="ISBN") as excinfo:
        server.get_book(book_id)

    assert "search_books" in str(excinfo.value)
    assert requests == []


@pytest.mark.parametrize("book_id", _MALFORMED)
def test_a_malformed_book_id_is_refused_before_any_request(requests, book_id):
    with pytest.raises(ValueError, match="book_id must be a Goodreads book id") as excinfo:
        server.get_book(book_id)

    message = str(excinfo.value)
    # names the accepted forms, and says what to do with a URL
    assert "'11870085-the-fault-in-our-stars'" in message
    assert "'2767052.The_Hunger_Games'" in message
    assert "URL pass just the number" in message
    assert requests == []


@pytest.mark.parametrize("tool", _GRAPHQL_TOOLS, ids=lambda t: t.__name__)
def test_graphql_tools_refuse_an_isbn_before_any_request(requests, tool):
    # Every GraphQL tool reads the number through _legacy_id; the refusal
    # cases themselves are covered through get_book above.
    with pytest.raises(ValueError, match="ISBN"):
        tool("978-0-593-13520-4")

    assert requests == []


def test_compare_books_reports_a_refused_id_on_its_own_entry(requests):
    result = server.compare_books(["54493401", "978-0-593-13520-4"])

    assert result["compared"] == 1
    refused = result["books"][-1]
    assert refused["book_id"] == "978-0-593-13520-4"
    assert "search_books" in refused["error"]
    assert requests == ["/book/show/54493401.xml"]


# ------------------------------------------------------------------- user_id


@pytest.mark.parametrize(
    ("user_id", "expected"),
    [("1", "1"), ("1-otis-chandler", "1-otis-chandler"), (" 222\n", "222")],
)
def test_user_id_accepts_the_number_with_an_optional_name_slug(
    monkeypatch, user_id, expected
):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", None)
    assert server._user_id(user_id) == expected


def test_a_blank_user_id_falls_back_to_the_stripped_default(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", " 111 ")
    assert server._user_id("  ") == "111"


@pytest.mark.parametrize(
    "user_id",
    [
        "01",
        "abc",
        "1.otis",
        "1-otis chandler",
        "1?shelf=read",
        "1#shelves",
        "../1",
        "https://www.goodreads.com/user/show/1-otis-chandler",
    ],
)
def test_a_malformed_user_id_is_refused_before_any_request(monkeypatch, requests, user_id):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", None)

    for tool in (server.get_shelf, server.list_shelves):
        with pytest.raises(ValueError, match="user_id must be a Goodreads user id"):
            tool(user_id=user_id)

    assert requests == []


def test_a_malformed_configured_user_id_is_refused_before_any_request(monkeypatch, requests):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "https://www.goodreads.com/user/show/1-otis-chandler")

    for tool in (server.get_shelf, server.list_shelves):
        with pytest.raises(ValueError, match="user_id must be a Goodreads user id"):
            tool()

    assert requests == []
