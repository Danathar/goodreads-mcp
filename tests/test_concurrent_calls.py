"""Offline tests for what happens when tool calls overlap (#92).

The MCP SDK runs a plain ``def`` tool on the event loop itself, so a tool that
blocks on Goodreads for its 30 s timeout used to block pings, cancellation and
every other call with it. ``OffLoopFastMCP`` registers each tool as a
coroutine that runs the body in a worker thread, and the client caps how many
requests those threads may have on the wire at once.

Every test here is deterministic: threads block on ``threading.Event`` and the
test decides when they may proceed, so a regression shows up as a definite
ordering failure (a call that could not have started did), never as a timing
flake. The one elapsed-time assertion has a two-second margin against a
five-second wait.
"""

from __future__ import annotations

import inspect
import threading
import time
from typing import Any

import anyio
import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from goodreads_mcp import server
from goodreads_mcp.client import MAX_IN_FLIGHT, GoodreadsClient

_WAIT = 5.0  # an upper bound on any blocking wait, so a regression fails, not hangs


class _Response:
    def __init__(self, payload: Any):
        self._payload = payload
        self.text = ""

    def json(self) -> Any:
        return self._payload


def _tool(name: str):
    return server.mcp._tool_manager.get_tool(name)


# ------------------------------------------------------------ registration


def test_every_tool_is_registered_as_a_coroutine_while_its_function_stays_sync():
    """The wrapper is what the SDK awaits; the module attribute is the body.

    ``compare_books`` calls ``get_book`` directly and the offline suite calls
    every body without an event loop, so both have to stay plain functions.
    """
    tools = server.mcp._tool_manager.list_tools()
    assert tools
    for tool in tools:
        assert tool.is_async, f"{tool.name} would run on the event loop"
        body = getattr(server, tool.name)
        assert not inspect.iscoroutinefunction(body), f"{tool.name} body went async"


def test_the_wrapper_advertises_the_same_schema_as_the_plain_function():
    """Clients see the schema FastMCP derives from the registered callable.

    ``functools.wraps`` carries the signature across; this pins that the
    parameters, output schema, description and annotations are the ones the
    body itself would produce, so nothing the client relies on changed.
    """
    plain = FastMCP("plain")
    for tool in server.mcp._tool_manager.list_tools():
        plain.add_tool(getattr(server, tool.name), annotations=tool.annotations)
    for ours, theirs in zip(
        server.mcp._tool_manager.list_tools(), plain._tool_manager.list_tools()
    ):
        assert ours.name == theirs.name
        assert ours.parameters == theirs.parameters, ours.name
        assert ours.output_schema == theirs.output_schema, ours.name
        assert ours.description == theirs.description, ours.name
        assert ours.annotations == theirs.annotations, ours.name


# ---------------------------------------------------------- the event loop


def test_a_tool_call_leaves_the_event_loop_free(monkeypatch):
    """While a tool blocks on Goodreads, the loop still runs other tasks.

    The fake request blocks until the test releases it. If the body ran on
    the loop, nothing else could run until it returned, so the check below
    would only be reached after the call had already produced its result.
    """
    started = threading.Event()
    release = threading.Event()

    def blocking_get(url: str, **kw: Any) -> _Response:
        started.set()
        release.wait(_WAIT)
        return _Response([{"bookId": "1", "title": "T", "bookUrl": "/book/show/1"}])

    monkeypatch.setattr(server.gr, "get", blocking_get)
    results: list[Any] = []

    async def main() -> None:
        async with anyio.create_task_group() as tg:

            async def call() -> None:
                results.append(await _tool("search_books").run({"query": "x"}))

            tg.start_soon(call)
            # This await runs on the loop while the tool is blocked.
            await anyio.to_thread.run_sync(started.wait, _WAIT)
            assert started.is_set()
            assert not results, "the tool ran to completion on the event loop"
            release.set()

    anyio.run(main)
    assert results and results[0][0]["title"] == "T"


def test_two_tool_calls_run_at_the_same_time(monkeypatch):
    """Two calls issued together are both in flight before either returns.

    On the loop, the second call could only start after the first had
    returned, so by the time both had entered the fake request the first
    would already be in ``finished``.
    """
    entered = threading.Semaphore(0)
    release = threading.Event()
    finished: list[str] = []

    def blocking_get(url: str, **kw: Any) -> _Response:
        entered.release()
        release.wait(_WAIT)
        finished.append(kw["params"]["q"])
        return _Response([])

    monkeypatch.setattr(server.gr, "get", blocking_get)

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            for query in ("a", "b"):
                tg.start_soon(_tool("search_books").run, {"query": query})
            for _ in range(2):
                assert await anyio.to_thread.run_sync(entered.acquire, True, _WAIT)
            assert finished == [], "the second call waited for the first to finish"
            release.set()

    anyio.run(main)
    assert sorted(finished) == ["a", "b"]


def test_a_tool_error_still_surfaces_as_a_tool_error(monkeypatch):
    """The thread hop must not swallow or re-shape the body's exception."""

    def failing_get(url: str, **kw: Any) -> _Response:
        raise ValueError("boom")

    monkeypatch.setattr(server.gr, "get", failing_get)

    async def main() -> None:
        with pytest.raises(ToolError, match="boom"):
            await _tool("search_books").run({"query": "x"})

    anyio.run(main)


def test_a_cancelled_call_returns_without_waiting_for_the_thread(monkeypatch):
    """Cancellation lets the request finish now; the thread winds down alone.

    The blocked thread only gets released after the cancelled scope has
    exited. Waiting for the thread instead would make the exit take the whole
    five-second wait, which is what the elapsed-time bound catches.
    """
    started = threading.Event()
    release = threading.Event()
    results: list[Any] = []

    def blocking_get(url: str, **kw: Any) -> _Response:
        started.set()
        release.wait(_WAIT)
        return _Response([])

    monkeypatch.setattr(server.gr, "get", blocking_get)

    async def main() -> float:
        async with anyio.create_task_group() as tg:

            async def call() -> None:
                results.append(await _tool("search_books").run({"query": "x"}))

            tg.start_soon(call)
            await anyio.to_thread.run_sync(started.wait, _WAIT)
            t0 = time.perf_counter()
            tg.cancel_scope.cancel()
        return time.perf_counter() - t0

    try:
        elapsed = anyio.run(main)
    finally:
        release.set()
    assert not results, "the call completed before it could be cancelled"
    assert elapsed < _WAIT - 2, f"cancellation waited {elapsed:.1f}s for the thread"


# -------------------------------------------------------------- the client


def _blocking_client(
    entered: threading.Semaphore, release: threading.Event, body: str = "{}"
) -> GoodreadsClient:
    """A client whose transport signals each request and blocks until released."""

    def handler(request: httpx.Request) -> httpx.Response:
        entered.release()
        release.wait(_WAIT)
        return httpx.Response(200, text=body)

    client = GoodreadsClient()
    client._client = httpx.Client(
        base_url="https://www.goodreads.com", transport=httpx.MockTransport(handler)
    )
    return client


def _run_threads(n: int, target) -> list[threading.Thread]:
    threads = [threading.Thread(target=target, daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    return threads


def test_no_more_than_max_in_flight_requests_reach_the_wire_at_once():
    """Worker threads may overlap; Goodreads sees at most MAX_IN_FLIGHT of them.

    More threads than slots call at once. Exactly MAX_IN_FLIGHT requests reach
    the transport and the rest wait; releasing the first wave lets the rest
    through, so every caller still completes.
    """
    entered = threading.Semaphore(0)
    release = threading.Event()
    client = _blocking_client(entered, release)
    done: list[int] = []
    callers = MAX_IN_FLIGHT + 2

    threads = _run_threads(callers, lambda: done.append(client.get("/x").status_code))
    for _ in range(MAX_IN_FLIGHT):
        assert entered.acquire(timeout=_WAIT), "a slot went unused"
    # The extra callers are queued on the semaphore, not on the wire.
    assert not entered.acquire(timeout=0.2), "a request above the cap reached the wire"

    release.set()
    for t in threads:
        t.join(_WAIT)
    assert done == [200] * callers


def test_the_cap_is_a_semaphore_not_a_lock():
    assert MAX_IN_FLIGHT >= 2, "one slot would serialize parallel tool calls again"
    assert isinstance(GoodreadsClient()._in_flight, threading.BoundedSemaphore)


def test_discovery_runs_once_when_calls_arrive_together():
    """Several first calls on a fresh process share one discovery.

    Without the lock each caller finds the cache empty and fetches the page
    and the bundle itself: 2 * callers requests instead of 2. The transport
    holds the first request until every caller is waiting, so the race is
    forced rather than hoped for.
    """
    page = (
        '<script src="/_next/static/chunks/pages/_app-deadbeef.js"></script>'
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"apiKey":"da2-abcdefghijklmnopqrstuvwxyz"}}}'
        "</script>"
    )
    bundle = (
        '"endpoint":"https://x.appsync-api.us-east-1.amazonaws.com/graphql"'
        ',"shortName":"Prod"'
    )
    callers = 4
    requests: list[str] = []
    all_waiting = threading.Semaphore(0)
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/giveaway":
            release.wait(_WAIT)
            return httpx.Response(200, text=page)
        return httpx.Response(200, text=bundle)

    client = GoodreadsClient()
    client._client = httpx.Client(
        base_url="https://www.goodreads.com", transport=httpx.MockTransport(handler)
    )
    configs: list[tuple[str, str]] = []

    def discover() -> None:
        all_waiting.release()
        configs.append(client.graphql_config())

    threads = _run_threads(callers, discover)
    for _ in range(callers):
        assert all_waiting.acquire(timeout=_WAIT)
    time.sleep(0.1)  # let every caller reach the lock (or, unlocked, the transport)
    release.set()
    for t in threads:
        t.join(_WAIT)

    assert requests == ["/giveaway", "/_next/static/chunks/pages/_app-deadbeef.js"]
    assert len(set(configs)) == 1 and len(configs) == callers


def test_the_session_is_created_once_across_threads(monkeypatch):
    """`single client, persistent session` holds when threads race the first use."""
    created: list[object] = []

    class SlowClient(httpx.Client):
        def __init__(self, **kw: Any):
            time.sleep(0.05)  # widen the window in which a second thread could build one
            created.append(self)
            super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    monkeypatch.setattr("goodreads_mcp.client.httpx.Client", SlowClient)
    client = GoodreadsClient()
    seen: list[object] = []

    threads = _run_threads(4, lambda: seen.append(client.client))
    for t in threads:
        t.join(_WAIT)

    assert len(created) == 1
    assert all(s is created[0] for s in seen) and len(seen) == 4
