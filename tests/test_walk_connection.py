"""`_walk_connection`: the one function that decides when a page walk stops.

Three loops over Goodreads cursors used to live in `server.py` — the
`_paginated_graphql_edges` helper, `get_reviews` and `popular_books` — and
each needed the same class of fix separately (#152, #160, #168). They also
disagreed: `popular_books` had no page cap and stopped on a page of only null
edges, while the other two did neither (#267). All three now walk through
`_walk_connection`, so its stopping rules are tested here, once, against the
walker itself rather than per tool:

* the page cap,
* a page of only null edges does not end the walk, an empty page does,
* a cursor already followed ends the walk,
* a last page that still carries a cursor (`hasNextPage` false) ends it,
* `cursor_only`, for `getReviews`, whose `hasNextPage` is always null and
  whose last page carries an empty cursor (checked live on 2026-10-03).

The middle section checks what each caller does with the walk: the request
budget, `has_more` and `totalCount`. The last section pins the structure: no tool body reads `pageInfo` or runs a
`while` loop of its own, so a fourth copy of the rules cannot creep back in.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from goodreads_mcp import server

_SERVER = Path(server.__file__)


class _Pages:
    """A `fetch_page` that serves queued pages and records the cursors it got."""

    def __init__(self, *pages: dict[str, Any]):
        self.pages = list(pages)
        self.tokens: list[str | None] = []

    def __call__(self, token: str | None) -> dict[str, Any]:
        self.tokens.append(token)
        if not self.pages:
            raise AssertionError(f"walker asked for a page past the last one (token {token!r})")
        return self.pages.pop(0)


def _page(edges: list[Any], token: str | None, has_next: bool | None = True) -> dict[str, Any]:
    return {"edges": edges, "pageInfo": {"hasNextPage": has_next, "nextPageToken": token}}


_EDGE = {"node": {"id": 1}}


def _walk(fetch: _Pages, max_pages: int = 10, **kwargs: Any) -> list[str | None]:
    return [next_token for _, next_token in server._walk_connection(fetch, max_pages, **kwargs)]


def test_the_cursor_of_each_page_is_passed_to_the_next_request():
    fetch = _Pages(_page([_EDGE], "a"), _page([_EDGE], "b"), _page([_EDGE], None, False))
    assert _walk(fetch) == ["a", "b", None]
    assert fetch.tokens == [None, "a", "b"]


def test_the_page_cap_ends_a_walk_that_never_ends_on_its_own():
    fetch = _Pages(*(_page([_EDGE], f"p{i}") for i in range(10)))
    assert len(_walk(fetch, max_pages=3)) == 3
    assert len(fetch.tokens) == 3


def test_a_page_of_only_null_edges_does_not_end_the_walk():
    fetch = _Pages(_page([None, {"node": None}], "a"), _page([_EDGE], None, False))
    assert _walk(fetch) == ["a", None]


def test_an_empty_page_ends_the_walk_but_still_reports_its_cursor():
    """The caller reads `has_more` off the cursor: Goodreads said more follow."""
    fetch = _Pages(_page([], "a"), _page([_EDGE], None, False))
    assert _walk(fetch) == ["a"]
    assert len(fetch.pages) == 1


def test_a_repeated_cursor_ends_the_walk():
    fetch = _Pages(_page([_EDGE], "a"), _page([_EDGE], "a"), _page([_EDGE], "a"))
    assert _walk(fetch) == ["a", "a"]
    assert fetch.tokens == [None, "a"]


@pytest.mark.parametrize("has_next", [False, None])
def test_a_last_page_that_still_carries_a_cursor_ends_the_walk(has_next):
    fetch = _Pages(_page([_EDGE], "stale", has_next), _page([_EDGE], None, False))
    assert _walk(fetch) == [None]
    assert len(fetch.pages) == 1


@pytest.mark.parametrize("token", [None, ""])
def test_a_page_with_no_cursor_ends_the_walk_even_if_it_says_more_follow(token):
    fetch = _Pages(_page([_EDGE], token, True), _page([_EDGE], None, False))
    assert _walk(fetch) == [None]


def test_cursor_only_follows_the_cursor_when_has_next_page_is_null():
    fetch = _Pages(_page([_EDGE], "a", None), _page([_EDGE], "", None))
    assert _walk(fetch, cursor_only=True) == ["a", None]
    assert fetch.tokens == [None, "a"]


@pytest.mark.parametrize("missing", [{}, None])
def test_a_missing_connection_or_page_info_is_a_last_page(missing):
    fetch = _Pages(missing, _page([_EDGE], None, False))
    assert _walk(fetch) == [None]


def test_a_cursor_that_comes_back_later_in_the_walk_ends_it():
    """Not only the cursor just followed: any cursor already followed."""
    fetch = _Pages(*(_page([_EDGE], token) for token in ("a", "b", "a", "b", "a")))
    assert _walk(fetch) == ["a", "b", "a"]
    assert fetch.tokens == [None, "a", "b"]


# ------------------------------------------------- what callers do with it


class _Graphql:
    """A `gr.graphql` stand-in: serves queued responses per query, counts calls."""

    def __init__(self, pages: dict[str, list[dict[str, Any]]]):
        self.pages = {query: list(queue) for query, queue in pages.items()}
        self.calls: list[str] = []

    def __call__(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(query)
        return self.pages[query].pop(0)

    def count(self, query: str) -> int:
        return self.calls.count(query)


_BOOK = {"getBookByLegacyId": {"legacyId": 1, "title": "A Book", "work": {"id": "kca://work/1"}}}


def _entries(n: int, start: int = 0) -> list[dict[str, Any]]:
    return [{"rank": start + i, "count": 1, "node": {"legacyId": start + i}} for i in range(n)]


@pytest.mark.parametrize(
    ("tool", "query", "pages"),
    [
        (lambda: server.popular_books(2024, limit=0), "_Q_TOP_LIST", {}),
        (lambda: server.get_reviews("1", limit=0), "_Q_REVIEWS", {"_Q_BOOK_BY_LEGACY": [_BOOK]}),
    ],
    ids=["popular_books", "get_reviews"],
)
def test_a_zero_limit_sends_no_page_request(monkeypatch, tool, query, pages):
    """`limit=0` asks for nothing, so it costs no page of results."""
    graphql = _Graphql({getattr(server, q): v for q, v in pages.items()})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = tool()

    assert graphql.count(getattr(server, query)) == 0
    assert (result["returned"], result["has_more"]) == (0, False)


def test_popular_books_has_no_more_when_its_last_page_exactly_fills_the_limit(monkeypatch):
    graphql = _Graphql({server._Q_TOP_LIST: [{"getTopList": _page(_entries(5), None, False)}]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    result = server.popular_books(2024, limit=5)

    assert (result["returned"], result["has_more"]) == (5, False)


def test_the_helper_keeps_the_total_count_of_the_first_page(monkeypatch):
    """A later page without `totalCount` must not erase it, or the unread
    remainder it proves stops showing up in `has_more`."""
    edges = [{"node": {"id": n}} for n in range(server._DISCOVERY_PAGE_SIZE)]
    first = {**_page(edges, "2"), "totalCount": 3 * len(edges)}
    graphql = _Graphql({"Q": [{"conn": first}, {"conn": _page(edges, None, False)}]})
    monkeypatch.setattr(server.gr, "graphql", graphql)

    found, has_more, total = server._paginated_graphql_edges("Q", "conn", {}, server._MAX_DISCOVERY)

    assert (len(found), has_more, total) == (2 * len(edges), True, 3 * len(edges))


def _after_a_page_of_nulls(size: int, want: int, connection: str) -> list[dict[str, Any]]:
    """One page of only null edges, then full pages until `want` is reached."""
    pages = [{connection: _page([None] * size, "1")}]
    for n in range(-(-want // size)):
        last = (n + 1) * size >= want
        edges = _entries(size, start=n * size)
        pages.append({connection: _page(edges, None if last else str(n + 2), not last)})
    return pages


def test_a_page_of_nulls_leaves_popular_books_room_for_a_full_walk(monkeypatch):
    """The page cap is twice a full walk so that a page of nulls, which no
    longer ends the walk (#267), cannot cut the chart short."""
    pages = _after_a_page_of_nulls(server._POPULAR_PAGE_SIZE, server._MAX_POPULAR, "getTopList")
    monkeypatch.setattr(server.gr, "graphql", _Graphql({server._Q_TOP_LIST: pages}))

    result = server.popular_books(2024, limit=server._MAX_POPULAR)

    assert result["returned"] == server._MAX_POPULAR


def test_a_page_of_nulls_leaves_the_helper_room_for_a_full_walk(monkeypatch):
    pages = _after_a_page_of_nulls(server._DISCOVERY_PAGE_SIZE, server._MAX_DISCOVERY, "conn")
    monkeypatch.setattr(server.gr, "graphql", _Graphql({"Q": pages}))

    found, has_more, _ = server._paginated_graphql_edges("Q", "conn", {}, server._MAX_DISCOVERY)

    assert (len(found), has_more) == (server._MAX_DISCOVERY, False)


_DESIGN = _SERVER.parent.parent / "docs" / "design.md"


def test_the_design_doc_states_the_page_caps_the_code_enforces():
    """`docs/design.md` is where a reader learns how many requests one call
    may cost; every number in its polite-client bullet is a cap in the code."""
    text = " ".join(_DESIGN.read_text(encoding="utf-8").split())
    bullet = text[text.index("**Polite client.**") :].split(" - **", 1)[0]
    stated = {
        "get_reviews": r"`get_reviews` caps paging at (\d+) reviews and (\d+) pages per call",
        "discovery": r"discovery tools page in batches of (\d+) up to (\d+) results and (\d+) pages",
        "popular_books": r"`popular_books` in batches of (\d+) up to (\d+) results and (\d+) pages",
    }
    found = {}
    for name, pattern in stated.items():
        match = re.search(pattern, bullet)
        assert match, f"docs/design.md no longer states the {name} caps as {pattern!r}"
        found[name] = tuple(int(n) for n in match.groups())

    assert found == {
        "get_reviews": (server._MAX_REVIEWS, server._MAX_REVIEW_PAGES),
        "discovery": (server._DISCOVERY_PAGE_SIZE, server._MAX_DISCOVERY, server._MAX_DISCOVERY_PAGES),
        "popular_books": (server._POPULAR_PAGE_SIZE, server._MAX_POPULAR, server._MAX_POPULAR_PAGES),
    }


# ---------------------------------------------------- one copy of the rules

def _functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(_SERVER.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _string_constants(node: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_only_the_walker_reads_page_info():
    readers = sorted(
        name
        for name, func in _functions().items()
        if {"pageInfo", "nextPageToken", "hasNextPage"} & _string_constants(func)
    )
    assert readers == ["_walk_connection"], (
        f"{readers} read a page's cursor; only `_walk_connection` should decide "
        "when a walk stops"
    )


def test_no_paginating_function_runs_its_own_loop_over_cursors():
    for name in ("_paginated_graphql_edges", "get_reviews", "popular_books"):
        func = _functions()[name]
        assert any(
            isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_walk_connection"
            for n in ast.walk(func)
        ), f"{name} no longer walks through `_walk_connection`"
        assert not any(isinstance(n, ast.While) for n in ast.walk(func)), (
            f"{name} has a `while` loop again; walk through `_walk_connection`"
        )
