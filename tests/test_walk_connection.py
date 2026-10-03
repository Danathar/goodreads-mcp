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

The last section pins the structure: no tool body reads `pageInfo` or runs a
`while` loop of its own, so a fourth copy of the rules cannot creep back in.
"""

from __future__ import annotations

import ast
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


def test_a_missing_connection_or_page_info_is_a_last_page():
    fetch = _Pages({}, _page([_EDGE], None, False))
    assert _walk(fetch) == [None]


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
