"""Offline tests for client.py's failure paths.

These are the branches that decide whether a Goodreads-side failure surfaces
as a clear error or as silently wrong data. The live suite cannot reach them:
a healthy Goodreads never returns a 503, a bundle with no Prod marker, or a
GraphQL body without `data`. Only fabricated responses get here, which is why
the live tier covers *less* of this module than the offline one.
"""

from __future__ import annotations

import json

import httpx
import pytest

from goodreads_mcp.client import (
    GoodreadsClient,
    GraphQLError,
    parse_appsync_endpoint,
    parse_page_api_key,
)


def _client(handler, **kw) -> GoodreadsClient:
    client = GoodreadsClient(**kw)
    client._client = httpx.Client(
        base_url="https://www.goodreads.com", transport=httpx.MockTransport(handler)
    )
    return client


# --------------------------------------------------------- _request backoff


def test_request_retries_a_429_then_returns_the_success(monkeypatch):
    statuses = iter((429, 200))
    monkeypatch.setattr("goodreads_mcp.client.time.sleep", lambda s: None)

    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        attempts.append(status)
        return httpx.Response(status, text="body")

    resp = _client(handler).get("/anything")

    assert resp.status_code == 200
    assert attempts == [429, 200]


def test_request_gives_up_after_max_retries_and_raises(monkeypatch):
    monkeypatch.setattr("goodreads_mcp.client.time.sleep", lambda s: None)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        return httpx.Response(503, text="unavailable")

    client = _client(handler, max_retries=3)
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        client.get("/anything")

    assert excinfo.value.response.status_code == 503
    # The last attempt must raise_for_status() rather than sleep and loop.
    assert len(attempts) == client.max_retries + 1 == 4


def test_request_backoff_delay_doubles(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("goodreads_mcp.client.time.sleep", slept.append)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler, max_retries=3).get("/anything")

    # One sleep per retry, not per attempt: the final attempt raises instead.
    assert len(slept) == 3
    # delay doubles from 1.0; each sleep adds up to 0.5s of jitter on top.
    for base, actual in zip((1.0, 2.0, 4.0), slept):
        assert base <= actual < base + 0.5


def test_request_does_not_retry_a_404(monkeypatch):
    monkeypatch.setattr("goodreads_mcp.client.time.sleep", lambda s: None)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        return httpx.Response(404, text="nope")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).get("/anything")

    assert len(attempts) == 1


def test_request_guard_fires_if_the_retry_loop_never_runs():
    # max_retries below zero makes the for-loop body unreachable. The guard
    # exists so that can never fall off the end returning None.
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not be requested")

    with pytest.raises(RuntimeError, match="unreachable"):
        _client(handler, max_retries=-1).get("/anything")


# ------------------------------------------------- parse_appsync_endpoint


ENDPOINT_PROD = "https://prod.appsync-api.us-east-1.amazonaws.com/graphql"
ENDPOINT_DEV = "https://dev.appsync-api.us-east-1.amazonaws.com/graphql"


def test_parse_appsync_endpoint_raises_when_bundle_has_none():
    with pytest.raises(ValueError, match="No AppSync endpoint"):
        parse_appsync_endpoint("var x = 1; // no appsync here")


def test_parse_appsync_endpoint_accepts_a_lone_endpoint_without_prod_marker():
    bundle = f'{{"appsync":{{"endpoint":"{ENDPOINT_PROD}"}},"shortName":"Staging"}}'
    assert parse_appsync_endpoint(bundle) == ENDPOINT_PROD


def test_parse_appsync_endpoint_refuses_to_guess_between_several():
    # The ambiguity guard: several environments, no Prod marker to pick one.
    # Refusing is the contract -- "just take the first" would ship the dev
    # endpoint to users with no error.
    bundle = (
        f'{{"Dev":{{"endpoint":"{ENDPOINT_DEV}"}},"shortName":"Dev",'
        f'"Staging":{{"endpoint":"{ENDPOINT_PROD}"}},"shortName":"Staging"}}'
    )
    with pytest.raises(ValueError, match="production AppSync endpoint"):
        parse_appsync_endpoint(bundle)


# ---------------------------------------------------- parse_page_api_key


def _next_data(payload: str) -> str:
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + payload
        + "</script>"
    )


@pytest.mark.parametrize(
    "html, why",
    [
        ("<html><body>no next data</body></html>", "no __NEXT_DATA__ blob"),
        (_next_data("{not json"), "invalid JSON"),
        (_next_data('["a", "list"]'), "payload is not an object"),
        (_next_data('{"pageProps": {}}'), "no props"),
        (_next_data('{"props": "a string"}'), "props is not an object"),
        (_next_data('{"props": {}}'), "no pageProps"),
        (_next_data('{"props": {"pageProps": 7}}'), "pageProps is not an object"),
    ],
)
def test_parse_page_api_key_returns_none_for_malformed_pages(html, why):
    # None is a *supported* outcome here -- it makes graphql_config fall back
    # to the legacy bundle pair. Each of these shapes must return rather than
    # raise, or a page-shape change becomes a hard failure instead of a
    # fallback.
    assert parse_page_api_key(html) is None, why


# -------------------------------------------------------- graphql_config


def _discovery_page(api_key: str | None, with_bundle: bool = True) -> str:
    page_props: dict = {"dataSource": "Production"}
    if api_key is not None:
        page_props["apiKey"] = api_key
    script = (
        '<script src="/_next/static/chunks/pages/_app-deadbeef.js"></script>'
        if with_bundle
        else ""
    )
    return script + _next_data(json.dumps({"props": {"pageProps": page_props}}))


LEGACY_BUNDLE = (
    '{"Prod":{"appsync":{"apiKey":"da2-legacykey00000000000000",'
    f'"endpoint":"{ENDPOINT_PROD}"' + ',"region":"us-east-1"},'
    '"shortName":"Prod"}}'
)


def test_graphql_config_raises_when_the_app_bundle_is_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_discovery_page(None, with_bundle=False))

    # First thing to break when Goodreads changes its page shell; it has to
    # say so rather than fail later as a confusing parse error.
    with pytest.raises(ValueError, match="Could not locate _app JS bundle"):
        _client(handler).graphql_config()


def test_graphql_config_falls_back_to_the_bundle_pair_when_the_page_key_is_absent():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/_next/"):
            return httpx.Response(200, text=LEGACY_BUNDLE)
        # A page whose Next data carries no apiKey at all.
        return httpx.Response(200, text=_discovery_page(None))

    endpoint, key = _client(handler).graphql_config()

    assert (endpoint, key) == (ENDPOINT_PROD, "da2-legacykey00000000000000")


# --------------------------------------------------------------- graphql


def _graphql_client(graphql_handler) -> tuple[GoodreadsClient, list[bool]]:
    """A client whose config discovery is canned, so graphql() is isolated.

    Returns (client, one `force` flag per graphql_config call).
    """
    client = _client(graphql_handler)
    config_calls: list[bool] = []

    def graphql_config(force: bool = False):
        config_calls.append(force)
        return ENDPOINT_PROD, "da2-prodkey0000000000000000"

    client.graphql_config = graphql_config  # type: ignore[method-assign]
    return client, config_calls


def test_graphql_propagates_a_500_without_rediscovering_the_key(monkeypatch):
    monkeypatch.setattr("goodreads_mcp.client.time.sleep", lambda s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    client, config_calls = _graphql_client(handler)
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        client.graphql("query { ping }")

    assert excinfo.value.response.status_code == 500
    # A 500 is not key rotation. Re-discovering would burn a round-trip and
    # mask a server-side outage as a config problem.
    assert config_calls == [False]


def test_graphql_raises_when_data_is_absent():
    errors = [{"message": "Validation failed"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": errors})

    client, _ = _graphql_client(handler)
    with pytest.raises(GraphQLError) as excinfo:
        client.graphql("query { ping }")

    # The message has to carry the errors; it is all a caller gets.
    assert "Validation failed" in str(excinfo.value)


def test_graphql_tolerates_field_level_errors_alongside_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"review": None, "book": {"title": "x"}},
                "errors": [{"message": "review was deleted"}],
            },
        )

    client, _ = _graphql_client(handler)
    # GraphQL partial success: a deleted sub-resource resolves to null and the
    # rest of the query still stands. This is the other half of the line the
    # docstring draws, and it is what keeps a deleted review from failing a
    # whole page of results.
    assert client.graphql("query { ping }") == {"review": None, "book": {"title": "x"}}
