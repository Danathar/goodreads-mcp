"""Start the server the way the shipped bundle starts it.

`manifest.json` gives every MCPB host one instruction for launching this
server -- `python -m goodreads_mcp.server`, with `PYTHONPATH` pointing at the
bundle root -- and until this file nothing ran it. Both suites reach the tools
by importing the module in-process, which never executes the
`if __name__ == "__main__":` guard, so deleting that guard left the whole
offline suite green while the manifest's command became a process that exits 0
without serving anything. The failure would have surfaced first in somebody's
client.

The approach is to *launch* it rather than assert about it:

* **The command is read out of the manifest, not hard-coded.** The test spawns
  whatever `server.mcp_config` says to spawn, after checking that what it says
  still describes this package: the `-m` argument has to name an importable
  module whose file is the manifest's own `entry_point`. Renaming
  `goodreads_mcp/server.py`, or moving `main` into a `__main__.py`, fails here
  instead of shipping a manifest that points at nothing.
* **A real handshake goes over the pipe.** `initialize`,
  `notifications/initialized` and `tools/list` are written to the child's
  stdin and its replies are parsed as JSON-RPC. This is offline and
  deterministic: the client opens no connection until a tool is actually
  called, so listing tools touches no Goodreads endpoint. It is also the only
  place the tool *surface* is checked as a client sees it, rather than as a
  Python import sees it.
* **The advertised tools are compared against the source in both directions.**
  The expected set is the functions carrying an `@mcp.tool` decorator, read out
  of `server.py` with `ast`, so a tool that stops being registered -- or one
  registered but never reaching the wire -- fails rather than silently
  shrinking what clients can call.
* **The read-only promise is checked where clients read it.** Every tool is
  decorated with `_READ_ONLY`, and that is the repository's central claim about
  itself; it reaches a client only as `annotations` in a `tools/list` reply, so
  that is where this asserts it.
* **The console script is resolved too.** `pyproject.toml` declares a second
  entry point into the same function, and it can drift from the module exactly
  as the manifest can.

Every subprocess here gets a timeout: a launch test that hangs on a server
waiting for input would otherwise stall CI rather than fail it.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import runpy
import subprocess
import sys
import threading
import time
import tomllib
import warnings

from mcp.server.fastmcp import FastMCP
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = _ROOT / "manifest.json"
_PYPROJECT = _ROOT / "pyproject.toml"
_SERVER_PY = _ROOT / "goodreads_mcp" / "server.py"

# The protocol revision this client claims. A server is free to answer with a
# different one; the handshake tests read what came back rather than pinning it.
_PROTOCOL = "2025-06-18"

_LAUNCH_TIMEOUT = 60

# The one request these tests send after the handshake. Listing tools is the
# only method that reports the whole surface without calling into a tool.
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mcp_config(manifest: dict) -> dict:
    return manifest["server"]["mcp_config"]


@pytest.fixture(scope="module")
def launch_argv(mcp_config: dict) -> list[str]:
    """The manifest's command, with its interpreter swapped for this one.

    The manifest says `python`, which is whatever the host resolves; the test
    has to run the interpreter the suite is running under so the child imports
    the same checkout.
    """
    command, *args = [mcp_config["command"], *mcp_config["args"]]
    assert Path(command).stem.startswith("python"), (
        f"manifest launches {command!r}, which this test's interpreter swap does not model"
    )
    return [sys.executable, *args]


def _module_under_dash_m(args: list[str]) -> str:
    """The module name `python -m` would import, or fail the calling test."""
    assert "-m" in args, f"manifest args {args!r} do not use `-m`; this test models that form"
    after = args[args.index("-m") + 1 :]
    assert after, f"manifest args {args!r} end at `-m` with no module to import"
    return after[0]


def _run(argv: list[str], stdin_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=_ROOT,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=_LAUNCH_TIMEOUT,
        check=False,
    )


def _handshake(
    argv: list[str], *requests: dict, env: dict[str, str] | None = None
) -> dict[int, dict]:
    """Initialize the server over stdio, then send `requests` down the same pipe.

    Replies are read while stdin is still open, and stdin is closed only once
    every request has been answered. Writing the whole script up front and then
    reading what the process happened to emit before EOF shut it down makes the
    last reply a race: the transport can tear the session down with a response
    still in flight.
    """
    ids = [request["id"] for request in requests if "id" in request]
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "test-stdio-launch", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        *requests,
    ]
    wanted = {1, *ids}
    replies: dict[int, dict] = {}
    lines: queue.Queue[str | None] = queue.Queue()

    with subprocess.Popen(
        argv,
        cwd=_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    ) as child:
        reader = threading.Thread(target=_pump, args=(child.stdout, lines), daemon=True)
        reader.start()
        try:
            child.stdin.write("".join(json.dumps(m) + "\n" for m in messages))
            child.stdin.flush()
            deadline = time.monotonic() + _LAUNCH_TIMEOUT
            while wanted - replies.keys():
                remaining = deadline - time.monotonic()
                assert remaining > 0, (
                    f"{argv} answered {sorted(replies)} and never "
                    f"{sorted(wanted - replies.keys())} within {_LAUNCH_TIMEOUT}s"
                )
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty:  # pragma: no cover - the assert above fires first
                    break
                assert line is not None, (
                    f"{argv} closed its output after answering {sorted(replies)}; "
                    f"still waiting for {sorted(wanted - replies.keys())}\n"
                    f"stderr:\n{child.stderr.read()}"
                )
                message = json.loads(line)
                if message.get("id") is not None:
                    replies[message["id"]] = message
        finally:
            child.stdin.close()
        stderr = child.stderr.read()
        assert child.wait(timeout=_LAUNCH_TIMEOUT) == 0, (
            f"{argv} exited {child.returncode} after the handshake\nstderr:\n{stderr}"
        )
    return replies


def _pump(stream, sink: "queue.Queue[str | None]") -> None:
    """Move the child's stdout lines onto `sink`, ending with a `None` at EOF."""
    for line in stream:
        line = line.strip()
        if line:
            sink.put(line)
    sink.put(None)


def _registered_tool_names() -> set[str]:
    """Functions decorated with `@mcp.tool` in `server.py`, read statically."""
    module = ast.parse(_SERVER_PY.read_text(encoding="utf-8"))
    names = set()
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            call = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(call, ast.Attribute) and call.attr == "tool":
                names.add(node.name)
    return names


# ------------------------------------------------------- the launch contract


def test_the_manifest_launches_an_importable_module(mcp_config: dict, manifest: dict):
    """`-m <module>` has to name a module whose file is the declared entry point."""
    module_name = _module_under_dash_m(list(mcp_config["args"]))
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin, (
        f"manifest launches `-m {module_name}`, which is not importable"
    )
    entry_point = (_ROOT / manifest["server"]["entry_point"]).resolve()
    assert Path(spec.origin).resolve() == entry_point, (
        f"`-m {module_name}` imports {spec.origin}, but the manifest's entry_point is {entry_point}"
    )


def test_the_manifest_puts_the_bundle_root_on_the_python_path(mcp_config: dict):
    """Without `${__dirname}` on `PYTHONPATH`, `-m goodreads_mcp.server` cannot resolve.

    The package is not installed inside an MCPB bundle; the host runs a plain
    `python` against unpacked files, so the bundle root is the only thing that
    makes the module importable.
    """
    path = mcp_config["env"]["PYTHONPATH"].split(":")
    assert "${__dirname}" in path, (
        f"PYTHONPATH {mcp_config['env']['PYTHONPATH']!r} does not include the bundle root"
    )


def test_the_module_runs_as_a_script_and_exits_cleanly(launch_argv: list[str]):
    """An empty stdin is EOF on the stdio transport: the server should stop, quietly.

    This is the test that executes the `if __name__ == "__main__":` guard. A
    module whose guard is gone also exits 0 here -- and answers nothing, which
    is what the handshake tests below catch.
    """
    result = _run(launch_argv, "")
    assert result.returncode == 0, f"{launch_argv} exited {result.returncode}\nstderr:\n{result.stderr}"
    assert result.stderr == "", f"{launch_argv} wrote to stderr on a clean shutdown:\n{result.stderr}"


def test_running_the_module_as_a_script_calls_main(monkeypatch: pytest.MonkeyPatch, mcp_config: dict):
    """The `__main__` guard has to call `main`, in-process where coverage sees it.

    The subprocess tests above prove the manifest's command serves requests, but
    coverage does not follow a child process: without this, the guard reads as
    an unexecuted line and a report reader cannot tell a tested launch path from
    an untested one. `runpy` executes the module under the name `python -m`
    gives it, against a `FastMCP.run` patched at the class level -- the reloaded
    module builds its own server instance, so patching the one this test
    imported would not reach it.
    """
    served: list[str] = []
    monkeypatch.setattr(FastMCP, "run", lambda self, *args, **kwargs: served.append(self.name))
    with warnings.catch_warnings():
        # `runpy` warns that the module is already imported, which is the point:
        # re-executing it is how the guard runs. Ignored rather than asserted so
        # the test does not depend on an interpreter keeping that warning.
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module(_module_under_dash_m(list(mcp_config["args"])), run_name="__main__")
    assert served == ["goodreads"], "running the module as a script did not start the server"


def test_the_launched_server_completes_a_protocol_handshake(launch_argv: list[str]):
    """The manifest's command must yield a server that answers `initialize`."""
    replies = _handshake(launch_argv)
    assert 1 in replies, "the launched server sent no reply to `initialize`"
    result = replies[1]["result"]
    assert result["serverInfo"]["name"] == "goodreads", (
        f"launched server identifies as {result['serverInfo']['name']!r}"
    )
    assert result["protocolVersion"], "the launched server named no protocol version"
    assert result["capabilities"]["tools"] is not None, "the launched server advertises no tools capability"


# ------------------------------------------------------- the tool surface


def test_the_launched_server_advertises_every_registered_tool(launch_argv: list[str]):
    """`tools/list` over the pipe has to match the `@mcp.tool` functions exactly."""
    replies = _handshake(launch_argv, _TOOLS_LIST)
    assert 2 in replies, "the launched server sent no reply to `tools/list`"
    advertised = {tool["name"] for tool in replies[2]["result"]["tools"]}
    registered = _registered_tool_names()
    assert registered, "no `@mcp.tool` function was found in server.py; this test cannot check anything"
    assert advertised == registered, (
        "the tools a client sees differ from the ones server.py registers: "
        f"only on the wire {sorted(advertised - registered)}, "
        f"only in the source {sorted(registered - advertised)}"
    )


def test_every_advertised_tool_tells_clients_it_is_read_only(launch_argv: list[str]):
    """The read-only promise reaches a client only as `tools/list` annotations."""
    replies = _handshake(launch_argv, _TOOLS_LIST)
    for tool in replies[2]["result"]["tools"]:
        annotations = tool.get("annotations") or {}
        assert annotations.get("readOnlyHint") is True, (
            f"{tool['name']} does not advertise readOnlyHint=true: {annotations}"
        )
        assert annotations.get("destructiveHint") is False, (
            f"{tool['name']} does not advertise destructiveHint=false: {annotations}"
        )


def test_the_handshake_reaches_no_network(launch_argv: list[str]):
    """Listing tools must stay offline, or the default suite stops being hermetic.

    The HTTP client is constructed lazily and only a tool call opens a
    connection, so pointing every proxy variable at a closed port changes
    nothing here -- and would fail loudly if that stopped being true and the
    default suite quietly started depending on the network.
    """
    env = dict(os.environ)
    env.update(
        {
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
    )
    replies = _handshake(launch_argv, _TOOLS_LIST, env=env)
    assert replies[2]["result"]["tools"], "`tools/list` returned nothing with the network unreachable"


# ------------------------------------------------------- the console script


def test_the_console_script_resolves_to_the_same_entry_function():
    """`pyproject`'s script and the `__main__` guard must call one function.

    A pip install exposes `goodreads-mcp`; the manifest's bundle does not use
    it, so it can drift from the module independently of everything above.
    """
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    target = pyproject["project"]["scripts"]["goodreads-mcp"]
    module_name, _, attribute = target.partition(":")
    assert attribute, f"console script target {target!r} names no function"
    module = importlib.import_module(module_name)
    assert callable(getattr(module, attribute, None)), (
        f"console script target {target!r} is not callable"
    )

    guard_calls = {
        node.func.id
        for block in ast.parse(_SERVER_PY.read_text(encoding="utf-8")).body
        if isinstance(block, ast.If)
        for node in ast.walk(block)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert attribute in guard_calls, (
        f"the console script calls {target!r}, but `server.py`'s `__main__` guard "
        f"calls {sorted(guard_calls)}"
    )
