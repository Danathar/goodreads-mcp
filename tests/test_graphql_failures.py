"""A failed GraphQL query must not read as an empty or complete answer (#202).

AppSync fails a query with HTTP 200: `{"data": {"<root>": null}, "errors":
[{"path": ["<root>"], "errorType": ..., "message": ...}]}`. Taken as partial
success, the null root became an empty connection, so a refused query read as
"no results" and a refused later page ended a list early with `has_more`
false. `GoodreadsClient.graphql` now raises `GraphQLError` for a failed root
field and still tolerates errors below one (a deleted review's sub-resource).
The tools turn the not-found cases into the errors they always gave: an
unknown book is "No book found", a chart Goodreads lacks names the period.

The other half of partial success is a null inside a list. A null edge, an edge
whose node is null, or a null series membership is skipped: it is not a result,
so it counts toward neither `limit` nor `has_more`.

The tool tests go through the real client with a mock transport, because the
root-field rule lives in `graphql()`; a stub for `gr.graphql` would skip it.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from goodreads_mcp import server
from goodreads_mcp.client import BASE, GoodreadsClient, GraphQLError

_ENDPOINT = "https://fake.appsync-api.us-east-1.amazonaws.com/graphql"
_BOOK = {
    "id": "kca://book/1",
    "legacyId": 1,
    "title": "A Book",
    "work": {"id": "kca://work/1"},
    "primaryContributorEdge": {"node": {"id": "kca://author/1", "name": "An Author"}},
    "bookSeries": [],
}


def _failed(root: str, error_type: str, message: str) -> dict[str, Any]:
    """The body AppSync sends when the `root` field failed."""
    return {
        "data": {root: None},
        "errors": [{"path": [root], "data": None, "errorType": error_type, "message": message}],
    }


# What AppSync sent live on 2026-09-27 for getBookByLegacyId(999999999) and for
# getTopList(works-by-release-date-3000).
_NO_BOOK = _failed(
    "getBookByLegacyId",
    "RESOURCE_NOT_FOUND",
    "Book not found (maybe deleted, and no replacement exists).",
)
_NO_CHART = _failed(
    "getTopList", "RESOURCE_NOT_FOUND", "A custom error was thrown from a mapping template."
)


def _root(query: str) -> str:
    """The query's one root field, e.g. `getSimilarBooks`."""
    return query.split("{", 2)[1].split("(")[0].split()[0]


@pytest.fixture
def appsync(monkeypatch):
    """Install `answer(root, variables) -> body` behind a real `server.gr`.

    Returns the (root, variables) of every POST, in order.
    """
    posted: list[tuple[str, dict[str, Any]]] = []

    def install(answer: Callable[[str, dict[str, Any]], dict[str, Any]]):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            root = _root(body["query"])
            posted.append((root, body["variables"]))
            return httpx.Response(200, json=answer(root, body["variables"]))

        client = GoodreadsClient()
        client._client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(handler))
        client._graphql_config = (_ENDPOINT, "da2-test")
        monkeypatch.setattr(server, "gr", client)
        return posted

    return install


def _connection(nodes: list[Any], token: str | None = None, **extra) -> dict[str, Any]:
    """A standard connection page with one edge per node."""
    return {
        "edges": [{"node": n} for n in nodes],
        "pageInfo": {"hasNextPage": token is not None, "nextPageToken": token},
        **extra,
    }


_NULL_EDGE = None
_NULL_NODE = {"node": None}


def _book(n: int) -> dict[str, Any]:
    return {"legacyId": n, "title": f"Book {n}", "webUrl": f"{BASE}/book/show/{n}"}


def _review(name: str) -> dict[str, Any]:
    return {"rating": 5, "text": "Good", "creator": {"name": name}}


# ------------------------------------------------------------- graphql()


_ROOT_FAILURE = _NO_CHART["errors"][0]


def _graphql_raising(body: dict[str, Any]) -> GraphQLError:
    client = GoodreadsClient()
    client._client = httpx.Client(
        base_url=BASE, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    )
    client._graphql_config = (_ENDPOINT, "da2-test")
    with pytest.raises(GraphQLError) as excinfo:
        client.graphql("query { getTopList { edges { rank } } }")
    return excinfo.value


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_NO_CHART, id="error-on-the-root-path"),
        # The same failure without a path still leaves the root field null.
        pytest.param(
            {
                "data": {"getTopList": None},
                "errors": [{k: v for k, v in _ROOT_FAILURE.items() if k != "path"}],
            },
            id="null-root-with-pathless-error",
        ),
    ],
)
def test_graphql_raises_when_a_root_field_failed(body):
    error = _graphql_raising(body)

    assert "RESOURCE_NOT_FOUND" in str(error)
    assert _ROOT_FAILURE["message"] in str(error)
    assert error.errors == body["errors"]
    assert error.not_found is True


def test_graphql_root_failure_keeps_only_the_errors_that_failed_the_query():
    """A sub-resource error riding along must not make a throttled root field
    look like a missing resource to a caller classifying the failure."""
    throttled = {"path": ["getTopList"], "errorType": "Throttling", "message": "Rate exceeded"}
    deleted = {"path": ["getTopList", "edges", 0], "errorType": "RESOURCE_NOT_FOUND"}

    error = _graphql_raising({"data": {"getTopList": None}, "errors": [deleted, throttled]})

    assert str(error) == "getTopList: Throttling: Rate exceeded"
    assert error.errors == [throttled]
    assert error.not_found is False


def test_graphql_names_a_body_with_neither_data_nor_errors():
    error = _graphql_raising({})

    assert "no data and no errors" in str(error)
    assert error.errors == []


# ------------------------------------------------------ failed root fields


def _similar_page_two_fails(root: str, variables: dict[str, Any]) -> dict[str, Any]:
    if root == "getBookByLegacyId":
        return {"data": {root: _BOOK}}
    if not variables["pagination"].get("after"):
        return {"data": {root: _connection([_book(n) for n in range(20)], "p2")}}
    return _failed(root, "Throttling", "Rate exceeded")


def _reviews_page_two_fails(root: str, variables: dict[str, Any]) -> dict[str, Any]:
    if root == "getBookByLegacyId":
        return {"data": {root: _BOOK}}
    if not variables["pagination"].get("after"):
        page = _connection([_review(str(n)) for n in range(30)], "p2", totalCount=90)
        return {"data": {root: page}}
    return _failed(root, "Throttling", "Rate exceeded")


def _chart_page_two_fails(root: str, variables: dict[str, Any]) -> dict[str, Any]:
    if variables["after"] is None:
        edges = [{"rank": n, "count": 1, "node": _book(n)} for n in range(30)]
        page = {"edges": edges, "pageInfo": {"hasNextPage": True, "nextPageToken": "p2"}}
        return {"data": {root: page}}
    return _NO_CHART


@pytest.mark.parametrize(
    "answer, call",
    [
        pytest.param(
            _similar_page_two_fails,
            lambda: server.similar_books("1", limit=40),
            id="similar_books",
        ),
        pytest.param(
            _reviews_page_two_fails,
            lambda: server.get_reviews("1", limit=60),
            id="get_reviews",
        ),
        # RESOURCE_NOT_FOUND after the chart's first page is not "no chart".
        pytest.param(
            _chart_page_two_fails,
            lambda: server.popular_books(2024, limit=50),
            id="popular_books",
        ),
    ],
)
def test_a_failed_later_page_raises_instead_of_ending_the_list(appsync, answer, call):
    """Before #202 these returned the first page with `has_more` false."""
    appsync(answer)

    with pytest.raises(GraphQLError):
        call()


def test_a_failed_first_page_raises_instead_of_reading_as_empty(appsync):
    def answer(root, variables):
        if root == "getBookByLegacyId":
            return {"data": {root: _BOOK}}
        return _failed(root, "Throttling", "Rate exceeded")

    appsync(answer)

    with pytest.raises(GraphQLError, match="getWorksByContributor: Throttling"):
        server.author_books("1")


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: server.similar_books("999999999"), id="via-_resolve_book_ids"),
        pytest.param(lambda: server.get_reviews("999999999"), id="get_reviews"),
    ],
)
def test_an_unknown_book_still_reads_no_book_found(appsync, call):
    posted = appsync(lambda root, variables: _NO_BOOK)

    with pytest.raises(ValueError, match="No book found for id '999999999'"):
        call()

    assert [root for root, _ in posted] == ["getBookByLegacyId"]


def test_a_book_lookup_that_fails_otherwise_is_not_reported_as_missing(appsync):
    appsync(lambda root, variables: _failed(root, "Throttling", "Rate exceeded"))

    with pytest.raises(GraphQLError, match="Throttling"):
        server.get_reviews("1")


@pytest.mark.parametrize(
    "month, chart, period",
    [
        (None, "works-by-release-date-3000", "3000"),
        (1, "books-by-release-date-3000-1", "3000-01"),
    ],
)
def test_popular_books_for_a_chart_goodreads_lacks_raises(appsync, month, chart, period):
    """Before #202 `popular_books(3000)` returned `returned: 0` -- an empty
    chart, as if nobody had added a book released that year."""
    posted = appsync(lambda root, variables: _NO_CHART)

    with pytest.raises(ValueError, match=f"no popular-by-date chart for {period}\\."):
        server.popular_books(3000, month=month)

    assert [variables["name"] for _, variables in posted] == [chart]


# ------------------------------------------------------------- null items


@pytest.mark.parametrize(
    "limit, edges, returned, has_more",
    [
        # Nulls are not results: they fill no slot and are not unread.
        (10, [_NULL_EDGE, {"node": _book(1)}, _NULL_NODE, {"node": _book(2)}], [1, 2], False),
        # A real book past the limit is unread.
        (1, [_NULL_EDGE, {"node": _book(1)}, _NULL_NODE, {"node": _book(2)}], [1], True),
        # Only nulls past the limit: nothing is left to read.
        (1, [{"node": _book(1)}, _NULL_EDGE, _NULL_NODE], [1], False),
    ],
)
def test_discovery_skips_null_edges_and_nodes(appsync, limit, edges, returned, has_more):
    def answer(root, variables):
        if root == "getBookByLegacyId":
            return {"data": {root: _BOOK}}
        # totalCount counts the null edges too; they must not read as unread.
        page = {**_connection([]), "edges": edges, "totalCount": len(edges)}
        return {"data": {root: page}}

    appsync(answer)

    result = server.similar_books("1", limit=limit)

    assert [b["book_id"] for b in result["similar"]] == [str(n) for n in returned]
    assert result["returned"] == len(returned)
    assert result["has_more"] is has_more


@pytest.mark.parametrize(
    "limit, reviewers, has_more",
    [
        (10, ["A", "B"], False),
        (1, ["A"], True),
    ],
)
def test_get_reviews_skips_null_edges_and_reviews(appsync, limit, reviewers, has_more):
    def answer(root, variables):
        if root == "getBookByLegacyId":
            return {"data": {root: _BOOK}}
        page = {
            "totalCount": 4,
            "edges": [_NULL_EDGE, _NULL_NODE, {"node": _review("A")}, {"node": _review("B")}],
            "pageInfo": {"nextPageToken": None},
        }
        return {"data": {root: page}}

    appsync(answer)

    result = server.get_reviews("1", limit=limit)

    assert [r["reviewer"] for r in result["reviews"]] == reviewers
    assert result["returned"] == len(reviewers)
    assert result["has_more"] is has_more


def test_get_reviews_does_not_count_a_trailing_null_as_unread(appsync):
    def answer(root, variables):
        if root == "getBookByLegacyId":
            return {"data": {root: _BOOK}}
        page = {
            "edges": [{"node": _review("A")}, _NULL_EDGE, _NULL_NODE],
            "pageInfo": {"nextPageToken": None},
        }
        return {"data": {root: page}}

    appsync(answer)

    result = server.get_reviews("1", limit=1)

    assert result["returned"] == 1
    assert result["has_more"] is False


_NULLS_THEN_REAL = {
    # tool: (page 1 of nulls only, page 2 with results, what each result is named by)
    "similar_books": (
        {"edges": [_NULL_NODE] * 3, "pageInfo": {"hasNextPage": True, "nextPageToken": "p2"}},
        _connection([_book(2), _book(3)]),
        ("similar", "book_id", ["2", "3"]),
    ),
    "get_reviews": (
        {"edges": [_NULL_EDGE, _NULL_NODE], "pageInfo": {"nextPageToken": "p2"}},
        {"edges": [{"node": _review("A")}], "pageInfo": {"nextPageToken": None}},
        ("reviews", "reviewer", ["A"]),
    ),
}


@pytest.mark.parametrize("tool", sorted(_NULLS_THEN_REAL))
def test_a_page_of_nulls_alone_does_not_end_the_walk(appsync, tool):
    """Only an empty page ends pagination; a page whose items are all null does not."""
    first, second, (key, field, expected) = _NULLS_THEN_REAL[tool]

    def answer(root, variables):
        if root == "getBookByLegacyId":
            return {"data": {root: _BOOK}}
        return {"data": {root: second if variables["pagination"].get("after") else first}}

    appsync(answer)

    result = getattr(server, tool)("1", limit=10)

    assert [item[field] for item in result[key]] == expected
    assert result["returned"] == len(expected)



def test_a_null_series_membership_is_skipped(appsync):
    series = {"userPosition": "2", "series": {"id": "kca://series/1", "title": "The Series"}}
    posted = appsync(
        lambda root, variables: {"data": {root: {**_BOOK, "bookSeries": [None, series]}}}
    )

    result = server.series_books("1", limit=0)

    assert result["series"] == "The Series"
    assert [root for root, _ in posted] == ["getBookByLegacyId"]


def test_get_book_skips_a_null_series_membership(monkeypatch):
    apollo = {
        "Book:1": {
            "legacyId": 1,
            "title": "A Book",
            "bookSeries": [None, {"userPosition": "3", "series": {"title": "The Series"}}],
        }
    }
    monkeypatch.setattr(server, "_fetch_book_apollo", lambda book_id: apollo)

    book = server.get_book("1")

    assert book["series"] == "The Series"
    assert book["series_position"] == "3"
    assert book["series_memberships"] == [{"series": "The Series", "position": "3"}]
