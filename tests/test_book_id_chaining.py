"""One tool's ``book_id`` feeds the next through the MCP layer (#193).

Every tool that takes a book declares ``book_id: str`` (``compare_books``
takes ``list[str]``), and pydantic refuses an integer for a string field. So
a ``book_id`` a tool *emits* must be a string too, or an agent that copies it
from one result into the next call gets a validation error. The GraphQL
tools used to copy ``legacyId`` -- a GraphQL ``Int`` -- straight through, and
the offline suite never noticed because it calls the tool functions directly,
below the layer that validates arguments.

These tests drive every tool through ``server.mcp.call_tool``, the same path
a host takes, collect every ``book_id`` in the result, and feed each one back
through ``server.mcp.call_tool`` into every tool that takes a ``book_id``.
Goodreads is faked with the smallest response each tool needs to emit an id.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from goodreads_mcp import server

_APOLLO = {
    "Book:1": {
        "legacyId": 1,
        "title": "A Book",
        "primaryContributorEdge": {"node": {"name": "An Author"}},
        "work": {"stats": {"averageRating": 4.2, "ratingsCount": 11}},
    }
}

_BOOK = {
    "id": "kca://book/1",
    "legacyId": 1,
    "title": "A Book",
    "work": {"id": "kca://work/1"},
    "primaryContributorEdge": {"node": {"id": "kca://author/9", "name": "An Author"}},
    "bookSeries": [{"userPosition": "1", "series": {"id": "kca://series/5", "title": "Arc"}}],
}


def _node(legacy_id: int) -> dict[str, Any]:
    return {
        "legacyId": legacy_id,
        "title": f"Book {legacy_id}",
        "webUrl": f"https://www.goodreads.com/book/show/{legacy_id}",
    }


def _page(edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"edges": edges, "pageInfo": {"hasNextPage": False, "nextPageToken": None}}


# The one GraphQL page each discovery tool needs to emit a book_id of its
# own. Every tool that resolves its input book first goes through
# ``_Q_BOOK_IDS``; ``get_reviews`` asks ``_Q_BOOK_BY_LEGACY`` instead.
_PAGES: dict[str, dict[str, Any]] = {
    server._Q_BOOK_IDS: {"getBookByLegacyId": _BOOK},
    server._Q_BOOK_BY_LEGACY: {"getBookByLegacyId": _BOOK},
    server._Q_REVIEWS: {"getReviews": {"totalCount": 0, **_page([])}},
    server._Q_SIMILAR: {"getSimilarBooks": _page([{"node": _node(2)}])},
    server._Q_AUTHOR: {"getWorksByContributor": _page([{"node": {"bestBook": _node(3)}}])},
    server._Q_SERIES: {
        "getWorksForSeries": _page(
            [{"node": {"bestBook": _node(4)}, "seriesPlacement": "1", "isPrimary": True}]
        )
    },
    server._Q_EDITIONS: {"getEditions": _page([{"node": _node(5)}])},
    server._Q_BOOK_LISTS: {"getBookListsOfBook": _page([{"node": {"legacyId": 77}}])},
    server._Q_TOP_LIST: {
        "getTopList": _page([{"rank": 1, "count": 9, "node": {"__typename": "Book", **_node(6)}}])
    },
}

_RSS = (
    "<rss><channel><item><title>A Book</title><book_id>7</book_id>"
    "<link>https://www.goodreads.com/review/show/7</link></item></channel></rss>"
)


class _Response:
    def __init__(self, payload: Any = None, text: str = ""):
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


@pytest.fixture
def faked_goodreads(monkeypatch):
    def get(url: str, **kw: Any) -> _Response:
        if url.startswith("/review/list_rss/"):
            return _Response(text=_RSS)
        return _Response([{"bookId": "8", "title": "A Book", "bookUrl": "/book/show/8"}])

    monkeypatch.setattr(server.gr, "get", get)
    monkeypatch.setattr(server.gr, "graphql", lambda query, variables: dict(_PAGES[query]))
    monkeypatch.setattr(server, "_fetch_book_apollo", lambda book_id: _APOLLO)


def _book_ids(value: Any) -> list[Any]:
    """Every ``book_id`` anywhere in a tool result, top level or nested."""
    if isinstance(value, dict):
        found = [value["book_id"]] if "book_id" in value else []
        return found + [i for v in value.values() for i in _book_ids(v)]
    if isinstance(value, list):
        return [i for v in value for i in _book_ids(v)]
    return []


async def _call(name: str, arguments: dict[str, Any]) -> Any:
    _content, structured = await server.mcp.call_tool(name, arguments)
    return structured


# The tools whose input schema has a ``book_id`` parameter, read off the
# schema a host sees rather than listed by hand.
_CONSUMERS = sorted(
    tool.name
    for tool in anyio.run(server.mcp.list_tools)
    if "book_id" in tool.inputSchema["properties"]
)


def test_seven_tools_take_a_book_id():
    """Pin the derived consumer list, so a tool that renames or drops the
    parameter cannot silently leave the round trip."""
    assert _CONSUMERS == [
        "author_books",
        "book_lists",
        "get_book",
        "get_editions",
        "get_reviews",
        "series_books",
        "similar_books",
    ]


_PRODUCERS = [
    ("search_books", {"query": "a book"}),
    ("get_book", {"book_id": "1"}),
    ("get_reviews", {"book_id": "1"}),
    ("similar_books", {"book_id": "1"}),
    ("author_books", {"book_id": "1"}),
    ("series_books", {"book_id": "1"}),
    ("get_editions", {"book_id": "1"}),
    ("book_lists", {"book_id": "1"}),
    ("popular_books", {"year": 2024}),
    ("compare_books", {"book_ids": ["1"]}),
    ("get_shelf", {"shelf": "read", "user_id": "42"}),
]


@pytest.mark.parametrize("producer, arguments", _PRODUCERS, ids=[p for p, _ in _PRODUCERS])
def test_every_emitted_book_id_feeds_every_tool_that_takes_one(
    faked_goodreads, producer, arguments
):
    async def main() -> None:
        ids = _book_ids(await _call(producer, arguments))
        assert ids, f"{producer} emitted no book_id; the fixture no longer exercises it"
        for book_id in ids:
            for consumer in _CONSUMERS:
                # A ToolError here is the bug: the id came out in a type the
                # argument schema refuses.
                await _call(consumer, {"book_id": book_id})
        await _call("compare_books", {"book_ids": ids})

    anyio.run(main)


def test_a_missing_legacy_id_stays_null_rather_than_becoming_the_string_none():
    """Partial GraphQL success leaves a sub-resource null; ``book_id`` must
    then be null too, not ``"None"``, which would look like a valid id."""
    assert server._book_summary({"title": "Orphan"})["book_id"] is None
    assert server._work_summary({"bestBook": None})["book_id"] is None
    assert server._node_summary({"__typename": "Work"})["book_id"] is None
