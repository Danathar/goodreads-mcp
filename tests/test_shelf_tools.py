"""Offline tests for the shelf tools and the user-id gate in front of them.

`list_shelves` is the one deliberate HTML scrape in this server, so its whole
contract is a regex plus a dedup loop: which `shelf=` occurrences count as a
shelf link, whether custom names are percent-decoded, and whether duplicates
collapse while keeping page order. The live suite can't pin any of that — a
healthy profile page exercises one shape, not the awkward ones.
"""

from __future__ import annotations

import httpx
import pytest

from goodreads_mcp import server
from goodreads_mcp.client import WAFChallenge


def _page(html: str, monkeypatch) -> list[str]:
    """Run list_shelves against a canned review-list page, recording the path."""
    calls: list[str] = []

    def get(url: str, **kw) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, text=html)

    monkeypatch.setattr(server.gr, "get", get)
    result = server.list_shelves()
    _page.last_calls = calls
    return result


# ------------------------------------------------------------------ _user_id


def test_user_id_prefers_the_explicit_argument(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "111")
    assert server._user_id("222") == "222"


def test_user_id_falls_back_to_the_configured_default(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "111")
    assert server._user_id(None) == "111"


def test_user_id_treats_an_empty_argument_as_absent(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "111")
    assert server._user_id("") == "111"


def test_user_id_stringifies_a_numeric_default(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", 12345678)
    assert server._user_id(None) == "12345678"


def test_user_id_raises_an_actionable_error_when_nothing_is_configured(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", None)
    with pytest.raises(ValueError) as excinfo:
        server._user_id(None)

    message = str(excinfo.value)
    # This is the first error a new user hits, and the only place that tells
    # them where their profile number lives.
    assert "GOODREADS_USER_ID" in message
    assert "goodreads.com/user/show/" in message


# -------------------------------------------------------------- list_shelves


def test_list_shelves_reads_the_review_list_page(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    shelves = _page('<a href="/review/list/9?shelf=read">read</a>', monkeypatch)

    assert shelves == ["read"]
    # Not /review/list_rss/: the RSS feed serves one shelf's items and cannot
    # enumerate shelf names, so that path swap would silently return nothing.
    assert _page.last_calls == ["/review/list/9"]


def test_list_shelves_uses_the_configured_default_user(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "12345678")
    _page("", monkeypatch)
    assert _page.last_calls == ["/review/list/12345678"]


def test_list_shelves_fails_before_the_network_when_no_user_is_configured(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", None)

    def get(url: str, **kw):  # pragma: no cover - must not be reached
        raise AssertionError(f"list_shelves hit the network: {url}")

    monkeypatch.setattr(server.gr, "get", get)
    with pytest.raises(ValueError):
        server.list_shelves()


def test_list_shelves_collects_first_and_later_query_positions(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    html = """
    <a href="/review/list/9?shelf=read">read</a>
    <a href="/review/list/9?page=2&shelf=to-read">to-read</a>
    """
    assert _page(html, monkeypatch) == ["read", "to-read"]


def test_list_shelves_ignores_shelf_without_a_parameter_boundary(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    # Unquoted attribute values, so the captured group would be non-empty if
    # the leading [?&] were dropped from the pattern.
    html = """
    <div data-bookshelf=not-a-shelf data-myshelf=also-not>x</div>
    <a href="/review/list/9?shelf=read">read</a>
    """
    assert _page(html, monkeypatch) == ["read"]


def test_list_shelves_percent_decodes_custom_names(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    html = '<a href="/review/list/9?shelf=sci-fi%20%26%20fantasy">x</a>'
    assert _page(html, monkeypatch) == ["sci-fi & fantasy"]


def test_list_shelves_dedupes_after_decoding_and_keeps_page_order(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    html = """
    <a href="?shelf=to-read">a</a>
    <a href="?shelf=read">b</a>
    <a href="?shelf=sci%2Dfi">c</a>
    <a href="?shelf=to-read">d</a>
    <a href="?shelf=sci-fi">e</a>
    """
    # sci%2Dfi and sci-fi decode to the same name, so they collapse to one
    # entry at the position where the first of them appeared.
    assert _page(html, monkeypatch) == ["to-read", "read", "sci-fi"]


def test_list_shelves_returns_empty_for_a_page_with_no_shelf_links(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    assert _page("<html><body>no shelves here</body></html>", monkeypatch) == []


def test_list_shelves_propagates_a_waf_challenge(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")

    def get(url: str, **kw):
        raise WAFChallenge("challenge")

    monkeypatch.setattr(server.gr, "get", get)
    # A challenge must not be read as "this user has no shelves".
    with pytest.raises(WAFChallenge):
        server.list_shelves()


# --------------------------------------------------------------------- main


def test_main_delegates_to_mcp_run(monkeypatch):
    ran: list[bool] = []
    monkeypatch.setattr(server.mcp, "run", lambda: ran.append(True))
    server.main()
    assert ran == [True]
