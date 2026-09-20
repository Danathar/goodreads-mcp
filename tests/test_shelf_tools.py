"""Offline tests for the shelf tools and the user-id gate in front of them.

`list_shelves` is the one deliberate HTML scrape in this server, so its whole
contract is a regex plus a dedup loop: which `shelf=` and `tag=` occurrences
count as a shelf link, whether custom names are percent-decoded, and whether
duplicates collapse while keeping page order. The live suite can't pin any of
that — a healthy profile page exercises one shape, not the awkward ones.

The two ways the page can be a non-answer are pinned here too: a private
profile (a 200 with a marker box where the shelves would be) and a sign-in
redirect (#91), which the client turns into `LoginRequired` before the regex
can mistake the login form for a user with no shelves.
"""

from __future__ import annotations

import httpx
import pytest

from goodreads_mcp import server
from goodreads_mcp.client import LoginRequired, WAFChallenge


def _run_list_shelves(html: str, monkeypatch) -> tuple[list[str], list[str]]:
    """Run list_shelves against a canned profile page.

    Returns (shelf names, paths requested).
    """
    paths: list[str] = []

    def get(url: str, **kw) -> httpx.Response:
        paths.append(url)
        return httpx.Response(200, text=html)

    monkeypatch.setattr(server.gr, "get", get)
    return server.list_shelves(), paths


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


def test_list_shelves_reads_the_profile_page(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    shelves, paths = _run_list_shelves(
        '<a href="/review/list/9?shelf=read">read</a>', monkeypatch
    )

    assert shelves == ["read"]
    # Not /review/list/9: that page redirects to sign-in (#91). And not
    # /review/list_rss/: the RSS feed serves one shelf's items and cannot
    # enumerate shelf names, so that path swap would silently return nothing.
    assert paths == ["/user/show/9"]


def test_list_shelves_uses_the_configured_default_user(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "12345678")
    _, paths = _run_list_shelves("", monkeypatch)
    assert paths == ["/user/show/12345678"]


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
    assert _run_list_shelves(html, monkeypatch)[0] == ["read", "to-read"]


def test_list_shelves_collects_custom_shelves_linked_as_tags(monkeypatch):
    """The profile's bookshelves module links the exclusive shelves as
    `?shelf=` and every custom shelf as `?tag=`. Both are shelves to the RSS
    feed (`/review/list_rss/9?shelf=sci-fi` works), so both are shelf names;
    reading only `shelf=` would hide every custom shelf."""
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    html = """
    <a href="/review/list/9?shelf=read">read (629)</a>
    <a href="/review/list/9?shelf=to-read">to-read (859)</a>
    <a href="/review/list/9?tag=sci-fi">sci-fi (65)</a>
    <a href="/review/list/9?tag=favorites">favorites (12)</a>
    """
    assert _run_list_shelves(html, monkeypatch)[0] == [
        "read", "to-read", "sci-fi", "favorites"
    ]


def test_list_shelves_ignores_shelf_without_a_parameter_boundary(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    # Unquoted attribute values, so the captured group would be non-empty if
    # the leading [?&] were dropped from the pattern.
    html = """
    <div data-bookshelf=not-a-shelf data-myshelf=also-not data-tag=nor-this>x</div>
    <a href="/review/list/9?shelf=read">read</a>
    """
    assert _run_list_shelves(html, monkeypatch)[0] == ["read"]


def test_list_shelves_percent_decodes_custom_names(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    html = '<a href="/review/list/9?shelf=sci-fi%20%26%20fantasy">x</a>'
    assert _run_list_shelves(html, monkeypatch)[0] == ["sci-fi & fantasy"]


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
    assert _run_list_shelves(html, monkeypatch)[0] == ["to-read", "read", "sci-fi"]


def test_list_shelves_returns_empty_for_a_page_with_no_shelf_links(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    html = "<html><body>no shelves here</body></html>"
    assert _run_list_shelves(html, monkeypatch)[0] == []


def test_list_shelves_propagates_a_waf_challenge(monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")

    def get(url: str, **kw):
        raise WAFChallenge("challenge")

    monkeypatch.setattr(server.gr, "get", get)
    # A challenge must not be read as "this user has no shelves".
    with pytest.raises(WAFChallenge):
        server.list_shelves()


def test_list_shelves_raises_for_a_private_profile(monkeypatch):
    """Goodreads serves a private profile as a 200 with a "This Profile is
    Private" box where the bookshelves module would be. Without the marker
    check that is indistinguishable from a public user with no shelves."""
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")
    html = """
    <div id="privateProfile" class="mediumText">
      This Profile is Private. <br/><br/> Sign in to Goodreads to Learn More.
    </div>
    """
    with pytest.raises(LoginRequired) as excinfo:
        _run_list_shelves(html, monkeypatch)
    assert "private" in str(excinfo.value)
    assert "9" in str(excinfo.value)


def test_list_shelves_raises_when_the_page_redirects_to_sign_in(monkeypatch):
    """#91 end to end: the profile path answers 302 -> /user/sign_in -> 200.

    Through the real client rather than a stubbed `get`, because the whole
    bug is that a followed redirect looks like a 200 to any caller that only
    checks status. The login form carries no shelf links, so before the
    client raised the tool returned [] and the caller read that as "no
    shelves"."""
    monkeypatch.setattr(server, "DEFAULT_USER_ID", "9")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/show/9":
            return httpx.Response(
                302, headers={"location": "/user/sign_in?returnurl=%2Fuser%2Fshow%2F9"}
            )
        assert request.url.path == "/user/sign_in"
        return httpx.Response(200, text="<html><form id='signIn'></form></html>")

    monkeypatch.setattr(
        server.gr,
        "_client",
        httpx.Client(
            base_url="https://www.goodreads.com",
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        ),
    )
    with pytest.raises(LoginRequired) as excinfo:
        server.list_shelves()
    assert "/user/show/9" in str(excinfo.value)
    assert "sign-in" in str(excinfo.value)


# --------------------------------------------------------------------- main


def test_main_delegates_to_mcp_run(monkeypatch):
    ran: list[bool] = []
    monkeypatch.setattr(server.mcp, "run", lambda: ran.append(True))
    server.main()
    assert ran == [True]
