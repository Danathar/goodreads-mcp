"""Offline tests: compare_books stops asking when Goodreads says to slow down (#437).

`_request` raises on a 429/503 it has given up on (a `Retry-After` above
MAX_RETRY_AFTER, or the retries spent). `compare_books` used to record that as
one more per-book error and send the next book at once, multiplying the load
at the moment Goodreads asked for less.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from goodreads_mcp import server
from goodreads_mcp.client import GoodreadsClient


def _page(rating: float, title: str) -> str:
    apollo = {
        "Book:1": {"title": title, "work": {"__ref": "Work:1"}},
        "Work:1": {"stats": {"averageRating": rating}},
    }
    data = {"props": {"pageProps": {"apolloState": apollo}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data)
        + "</script>"
    )


def _install(monkeypatch, answers: dict[str, httpx.Response]) -> list[str]:
    """Route server.gr through a mock transport; `answers` maps path to reply."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return answers[request.url.path]

    client = GoodreadsClient()
    client._client = httpx.Client(
        base_url="https://www.goodreads.com", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(server, "gr", client)
    monkeypatch.setattr("goodreads_mcp.client.time.sleep", lambda s: None)
    return sent


def _path(book_id: str) -> str:
    return f"/book/show/{book_id}.xml"


def _all(status: int, headers: dict[str, str] | None = None) -> dict[str, httpx.Response]:
    return {_path(str(n)): httpx.Response(status, headers=headers) for n in range(1, 11)}


_IDS = [str(n) for n in range(1, 11)]


def test_a_long_retry_after_stops_after_one_request(monkeypatch):
    sent = _install(monkeypatch, _all(429, {"Retry-After": "3600"}))

    result = server.compare_books(_IDS)

    assert sent == [_path("1")]
    assert result["compared"] == 0


def test_a_plain_429_stops_after_one_books_own_four_attempts(monkeypatch):
    sent = _install(monkeypatch, _all(429))

    server.compare_books(_IDS)

    assert sent == [_path("1")] * 4


def test_a_503_stops_the_same_way(monkeypatch):
    sent = _install(monkeypatch, _all(503))

    server.compare_books(_IDS)

    assert sent == [_path("1")] * 4


def test_the_refused_book_keeps_its_error_and_the_rest_say_not_fetched(monkeypatch):
    _install(monkeypatch, _all(429, {"Retry-After": "3600"}))

    books = server.compare_books(_IDS[:3])["books"]

    assert [b["book_id"] for b in books] == ["1", "2", "3"]
    assert "429" in books[0]["error"]
    assert "Not fetched" not in books[0]["error"]
    for skipped, code in zip(books[1:], ("2", "3")):
        assert skipped["error"] == (
            "Not fetched: Goodreads asked to slow down (HTTP 429), so no more "
            "books were requested. Try again later."
        )
        assert skipped["book_id"] == code


def test_a_503_names_its_status_in_the_not_fetched_entries(monkeypatch):
    _install(monkeypatch, _all(503, {"Retry-After": "3600"}))

    books = server.compare_books(["1", "2"])["books"]

    assert "(HTTP 503)" in books[1]["error"]


def test_books_fetched_before_a_refusal_are_still_ranked(monkeypatch):
    answers: dict[str, httpx.Response] = {
        _path("1"): httpx.Response(200, text=_page(3.5, "Low")),
        _path("2"): httpx.Response(200, text=_page(4.5, "High")),
        _path("3"): httpx.Response(429, headers={"Retry-After": "3600"}),
        _path("4"): httpx.Response(200, text=_page(5.0, "Never asked")),
    }
    sent = _install(monkeypatch, answers)

    result = server.compare_books(["1", "2", "3", "4"])

    assert sent == [_path("1"), _path("2"), _path("3")]
    assert result["compared"] == 2
    books: list[dict[str, Any]] = result["books"]
    assert [b.get("title") for b in books[:2]] == ["High", "Low"]
    assert [b["book_id"] for b in books[2:]] == ["3", "4"]
    assert all("error" in b for b in books[2:])


def test_a_404_still_carries_on_to_the_next_book(monkeypatch):
    answers: dict[str, httpx.Response] = {
        _path("1"): httpx.Response(404),
        _path("2"): httpx.Response(200, text=_page(4.0, "Fine")),
    }
    sent = _install(monkeypatch, answers)

    result = server.compare_books(["1", "2"])

    assert sent == [_path("1"), _path("2")]
    assert result["compared"] == 1
    assert result["books"][0]["title"] == "Fine"
    assert "404" in result["books"][1]["error"]
    assert "Not fetched" not in result["books"][1]["error"]


def test_the_docstring_tells_a_caller_the_comparison_can_stop_early():
    # Collapse the wrapping so the check reads the sentence, not the line breaks.
    doc = " ".join((server.compare_books.__doc__ or "").split())
    assert "slow down" in doc
    assert "not fetched" in doc


@pytest.mark.parametrize("status", [429, 503])
def test_a_refusal_that_arrives_on_the_last_book_adds_no_extra_entries(
    monkeypatch, status
):
    _install(monkeypatch, _all(status, {"Retry-After": "3600"}))

    books = server.compare_books(["1"])["books"]

    assert len(books) == 1
