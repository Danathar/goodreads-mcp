"""`prompts/diagnose-parse-regression.md`, read as a subject.

The prompt is what an agent is handed when a tool starts returning wrong or
empty data. Its step 3 is a routing table: each bullet names what the reader
sees (an exception, an empty result) and says which upstream change produced
it. Before this file the only test that opened the prompt was
`test_check_live_endpoints_skill.py`, which takes the live-suite command out of
it and nothing else, so every route in step 3 was unread.

One route was wrong, in the prompt and in the check-live-endpoints skill's
symptom table alike: both sent `GraphQLError` to "key/endpoint rotation". The
client raises `GraphQLError` only when AppSync answers without `data`, which is
what a query naming a renamed field gets ("Validation error of type
FieldUndefined", seen live on 2026-09-25); a rotated key is an HTTP 401 that
`graphql` re-discovers past on its own, and surfaces only as an
`httpx.HTTPStatusError` when re-discovery does not help. And the failure the
prompt pointed at -- "the discovery regexes in `client.py` need updating" --
raises neither: it is a `ValueError` out of `graphql_config`, which no route
named. Someone following the prompt after a schema change would have gone
looking at key discovery that was working.

So the routes are checked by provoking each upstream change against the real
code (`_SCENARIOS`), taking the exception it actually raises, finding the one
route in each copy that names that exception, and requiring the route to name
the cause. The prompt and the skill are held to the same scenarios, which is
what keeps the two copies from drifting apart again. The literal details the
prompt quotes (statuses, endpoints, link params, the example message) are read
out of the code rather than restated.
"""

from __future__ import annotations

import ast
import json
import re
import traceback
from pathlib import Path

import httpx
import pytest

from goodreads_mcp import client as client_mod
from goodreads_mcp import server
from goodreads_mcp.client import (
    GoodreadsClient,
    GraphQLError,
    LoginRequired,
    WAFChallenge,
)

_ROOT = Path(__file__).resolve().parents[1]
_PROMPT = _ROOT / "prompts" / "diagnose-parse-regression.md"
_SKILL = _ROOT / ".claude" / "skills" / "check-live-endpoints" / "SKILL.md"
_CLIENT_PY = _ROOT / "goodreads_mcp" / "client.py"
_SERVER_PY = _ROOT / "goodreads_mcp" / "server.py"
_AGENTS = _ROOT / "AGENTS.md"

# How step 2 spells each surface, mapped to AGENTS.md's `## The data surfaces`
# bold name. Asserted to cover all of them, in AGENTS.md's order.
_SURFACE_ALIASES = {
    "shelf RSS": "Shelf RSS",
    "JSON autocomplete": "Search autocomplete",
    "`__NEXT_DATA__`": "Embedded page JSON",
    "AppSync GraphQL": "AppSync GraphQL",
    "best-effort HTML scrape": "Scraped HTML",
}


# ----------------------------------------------------------------- readers


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _text() -> str:
    return _PROMPT.read_text(encoding="utf-8")


def _step(number: int) -> str:
    """Body of numbered step `number`, up to the next numbered step."""
    match = re.search(
        rf"^{number}\. (.*?)(?=^\d+\. |^Constraints:|\Z)", _text(), re.M | re.S
    )
    assert match, f"step {number} is gone"
    return match.group(1)


def _prompt_routes() -> list[tuple[str, str]]:
    """Step 3's bullets as (what the reader sees, what it means)."""
    bullets = re.split(r"^   - ", _step(3), flags=re.M)[1:]
    routes = []
    for bullet in bullets:
        symptom, arrow, cause = _squash(bullet).partition(" → ")
        assert arrow, f"bullet has no → route: {bullet!r}"
        routes.append((symptom, cause))
    return routes


def _skill_routes() -> list[tuple[str, str]]:
    """The skill's symptom table as (symptom, what happened)."""
    section = _SKILL.read_text(encoding="utf-8").split(
        "## Read the failure by surface", 1
    )[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells) or cells[0] == "symptom":
            continue
        rows.append((cells[0], cells[2]))
    return rows


def _route(routes, exception: str, where: str | None) -> str:
    """The cause of the one route whose symptom names `exception` in
    backticks (and `where`, when two routes name the same exception)."""
    hits = [
        cause
        for symptom, cause in routes
        if re.search(rf"`(?:[\w.]*\.)?{exception}\b", symptom)
        and (where is None or where in symptom)
    ]
    assert len(hits) == 1, f"expected one route for {exception} ({where}), got {hits}"
    return hits[0]


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _compared_statuses(function: ast.FunctionDef) -> set[int]:
    """The status tuple `function` tests with `status_code not in (...)`."""
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Attribute)
            and node.left.attr == "status_code"
            and isinstance(node.ops[0], ast.NotIn)
        ):
            return {elt.value for elt in node.comparators[0].elts}
    raise AssertionError(f"{function.name} no longer compares a status tuple")


# --------------------------------------------------------------- fixtures


_FAKE_KEY = "da2-abcdefghijklmnop"
_FAKE_ENDPOINT = "https://fake123.appsync-api.us-east-1.amazonaws.com/graphql"
_APP_CHUNK = "/_next/static/chunks/pages/_app-0123abcd.js"
_NEXT_DATA = (
    '<script id="__NEXT_DATA__" type="application/json">'
    + json.dumps({"props": {"pageProps": {"apiKey": _FAKE_KEY}}})
    + "</script>"
)
_CONFIG_PAGE = f'<html><script src="{_APP_CHUNK}"></script>{_NEXT_DATA}</html>'
_APP_BUNDLE = '{"endpoint":"' + _FAKE_ENDPOINT + '","shortName":"Prod"}'


@pytest.fixture
def goodreads(monkeypatch):
    """Install `handler` behind a fresh `server.gr` and record the requests.

    The transport goes on the `client` property: `_client` is a dataclass
    field that `__init__` resets, so patching it builds a real client.
    """
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    seen: list[tuple[str, str]] = []

    def install(handler):
        def recording(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            return handler(request)

        transport = httpx.Client(
            base_url="https://www.goodreads.com",
            transport=httpx.MockTransport(recording),
            follow_redirects=True,
        )
        monkeypatch.setattr(GoodreadsClient, "client", property(lambda s: transport))
        monkeypatch.setattr(server, "gr", GoodreadsClient())
        return seen

    return install


def _graphql_site(page=_CONFIG_PAGE, bundle=_APP_BUNDLE, post=None):
    """Discovery page + `_app` bundle + a GraphQL endpoint answering `post`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return post(request)
        if request.url.path == client_mod.CONFIG_DISCOVERY_PATH:
            return httpx.Response(200, text=page)
        if request.url.path == _APP_CHUNK:
            return httpx.Response(200, text=bundle)
        return httpx.Response(404)

    return handler


def _raised(install, handler, call) -> BaseException:
    install(handler)
    with pytest.raises(Exception) as excinfo:
        call()
    return excinfo.value


# Each upstream change the prompt routes, provoked through a real tool. The
# fields: the change, the site that shows it, the tool call, the exception the
# code raises, the function it must be raised through (or None), and a pattern
# the route's cause must match in every copy.
_SCENARIOS = {
    "schema field renamed": (
        _graphql_site(
            post=lambda r: httpx.Response(
                200,
                json={"errors": [{"message": "Validation error of type FieldUndefined"}]},
            )
        ),
        lambda: server.similar_books("1"),
        GraphQLError,
        None,
        r"\brenamed\b",
    ),
    "key rotated and re-discovery did not help": (
        _graphql_site(post=lambda r: httpx.Response(401, json={})),
        lambda: server.similar_books("1"),
        httpx.HTTPStatusError,
        None,
        r"\brotat",
    ),
    "_app bundle changed shape": (
        _graphql_site(bundle="window.config = {}"),
        lambda: server.similar_books("1"),
        ValueError,
        "graphql_config",
        r"`_app` bundle changed shape",
    ),
    "discovery page lost its _app script": (
        _graphql_site(page="<html></html>"),
        lambda: server.similar_books("1"),
        ValueError,
        "graphql_config",
        r"discovery page",
    ),
    "book page WAF-gated": (
        lambda r: httpx.Response(
            202,
            headers={"content-type": "text/html"},
            text=f"<html>{client_mod.WAF_MARKERS[0]}</html>",
        ),
        lambda: server.get_book("1"),
        WAFChallenge,
        None,
        r"\bWAF\b",
    ),
}

# Routes that name no exception the scenarios raise, by the phrase that
# identifies them; each has its own test below.
_NON_EXCEPTION_ROUTES = ("Empty/None fields from `__NEXT_DATA__`", "`list_shelves` empty")


# ------------------------------------------------ the routes, by scenario


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
@pytest.mark.parametrize("copy", ["prompt", "skill"])
def test_each_upstream_change_is_routed_to_its_cause(goodreads, scenario, copy):
    handler, call, expected, through, cause_pattern = _SCENARIOS[scenario]
    raised = _raised(goodreads, handler, call)
    assert type(raised) is expected, f"{scenario}: raised {raised!r}"
    if through:
        frames = {f.name for f in traceback.extract_tb(raised.__traceback__)}
        assert through in frames, f"{scenario}: not raised through {through}"

    routes = _prompt_routes() if copy == "prompt" else _skill_routes()
    cause = _route(routes, expected.__name__, through)
    assert re.search(cause_pattern, cause), f"{copy} routes {scenario} to {cause!r}"


@pytest.mark.parametrize("copy", ["prompt", "skill"])
def test_graphql_error_is_not_routed_to_rotation(copy):
    """The defect this file was written for: a dataless answer is a rejected
    query, and saying "rotation" sends the reader to key discovery instead."""
    routes = _prompt_routes() if copy == "prompt" else _skill_routes()
    symptom = next(s for s, _ in routes if "`GraphQLError`" in s)
    assert not re.search(r"\d{3}", symptom), "401/403 is a separate route"
    cause = _route(routes, "GraphQLError", None)
    assert not re.search(r"\brotated\b(?! key)", cause), cause


def test_every_step_3_route_is_a_scenario_or_named_here():
    """A new route must be provoked (a `_SCENARIOS` row) or listed."""
    scenario_exceptions = {s[2].__name__ for s in _SCENARIOS.values()}
    for symptom, _ in _prompt_routes():
        named = set(re.findall(r"`(?:[\w.]*\.)?(\w+)`", symptom)) & scenario_exceptions
        listed = [p for p in _NON_EXCEPTION_ROUTES if symptom.startswith(p)]
        assert bool(named) != bool(listed), f"unclassified route: {symptom!r}"
    for phrase in _NON_EXCEPTION_ROUTES:
        assert any(s.startswith(phrase) for s, _ in _prompt_routes()), phrase


def test_every_exception_the_client_defines_is_named_in_the_prompt():
    tree = ast.parse(_CLIENT_PY.read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(getattr(b, "id", "") == "Exception" for b in node.bases)
    }
    assert defined == {"WAFChallenge", "LoginRequired", "GraphQLError"}
    named = set(re.findall(r"`(?:httpx\.)?([A-Z]\w+(?:Error|Challenge|Required))`", _text()))
    assert defined <= named
    assert named - defined <= {"ValueError", "HTTPStatusError"}


# ------------------------------------------------------ the GraphQL routes


def test_the_quoted_discovery_message_is_the_one_a_changed_bundle_raises(goodreads):
    quoted = re.search(r'`graphql_config` \(such as "([^"]+)"\)', _squash(_step(3)))
    assert quoted, "the ValueError route no longer quotes an example message"
    handler, call, *_ = _SCENARIOS["_app bundle changed shape"]
    assert str(_raised(goodreads, handler, call)) == quoted.group(1)


def test_the_rotation_route_statuses_are_the_ones_graphql_re_discovers_on(goodreads):
    symptom = next(s for s, _ in _prompt_routes() if "HTTPStatusError" in s)
    named = {int(s) for s in re.findall(r"\b(\d{3})\b", symptom)}
    assert named == _compared_statuses(_function(_CLIENT_PY, "graphql"))

    # "already re-discovers once": a persisting 401 costs two discovery
    # rounds and two POSTs, then surfaces.
    handler, call, *_ = _SCENARIOS["key rotated and re-discovery did not help"]
    seen = goodreads(handler)
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        call()
    assert excinfo.value.response.status_code in named
    assert [m for m, _ in seen].count("POST") == 2
    assert seen.count(("GET", client_mod.CONFIG_DISCOVERY_PATH)) == 2


def test_a_single_rotation_self_heals_so_it_is_not_a_symptom(goodreads):
    answers = iter((401, 200))

    def post(request):
        status = next(answers)
        if status != 200:
            return httpx.Response(status, json={})
        return httpx.Response(200, json={"data": {"ok": True}})

    goodreads(_graphql_site(post=post))
    assert server.gr.graphql("query { x }") == {"ok": True}


def test_the_rate_limit_statuses_are_the_ones_request_retries():
    named = {int(s) for s in re.findall(r"\((\d{3})/(\d{3}) exhausting", _step(1))[0]}
    assert named == _compared_statuses(_function(_CLIENT_PY, "_request"))


# --------------------------------------------------- the WAF and surfaces


def test_the_waf_alternates_are_the_ones_the_exception_suggests(goodreads):
    listed = re.search(r"alternate endpoint \(([^)]+)\)", _squash(_step(3))).group(1)
    alternates = [a.replace("`", "").strip() for a in listed.split(",")]

    handler, call, *_ = _SCENARIOS["book page WAF-gated"]
    message = str(_raised(goodreads, handler, call))
    suggested = re.search(r"\(e\.g\. (.+?)\)", message).group(1)
    assert len(re.split(r",? or |, ", suggested)) == len(alternates)
    for alternate in alternates:
        assert alternate in suggested, f"{alternate!r} is not in {suggested!r}"


def test_step_2_names_the_surfaces_agents_md_lists_in_order():
    agents = _AGENTS.read_text(encoding="utf-8")
    section = agents.split("## The data surfaces", 1)[1].split("\n## ", 1)[0]
    documented = re.findall(r"^\d+\. \*\*(.+?)\*\*", section, re.M)

    step = _squash(_step(2))
    found = sorted((step.index(alias), name) for alias, name in _SURFACE_ALIASES.items())
    assert [name for _, name in found] == documented
    assert "for `list_shelves` only — a best-effort HTML scrape" in step


# ------------------------------------------------------ the list_shelves route


def _list_shelves_route() -> str:
    return next(c for s, c in _prompt_routes() if s.startswith("`list_shelves` empty"))


def test_the_list_shelves_route_names_the_page_and_params_the_tool_reads():
    route = _list_shelves_route()
    source = ast.get_source_segment(
        _SERVER_PY.read_text(encoding="utf-8"), _function(_SERVER_PY, "list_shelves")
    )
    params = re.search(r"\(\?:(\w+(?:\|\w+)*)\)=", source).group(1).split("|")
    assert set(re.findall(r"`(\w+)=`", route)) == set(params)

    page = re.search(r"`(/user/show/\{uid\})`", route).group(1)
    assert f'gr.get(f"{page}")' in source


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            lambda r: httpx.Response(
                200, text=f"<html><div {server._PRIVATE_PROFILE_MARKER}></div></html>"
            ),
            id="private-profile",
        ),
        pytest.param(
            lambda r: (
                httpx.Response(200, text="<form>sign in</form>")
                if r.url.path == client_mod.SIGN_IN_PATH
                else httpx.Response(302, headers={"location": client_mod.SIGN_IN_PATH})
            ),
            id="sign-in-redirect",
        ),
    ],
)
def test_a_private_or_sign_in_profile_raises_rather_than_returning_empty(
    goodreads, response
):
    route = _list_shelves_route()
    assert "A private profile and a sign-in redirect raise `LoginRequired`" in route
    assert re.search(r"\(#(\d+)\)", route).group(1) in LoginRequired.__doc__
    goodreads(response)
    with pytest.raises(LoginRequired):
        server.list_shelves("1234")


def test_a_public_profile_whose_links_moved_returns_empty(goodreads):
    assert "if you see `[]` for a public user, the links moved" in _list_shelves_route()
    goodreads(lambda r: httpx.Response(200, text="<html><a href='/shelves'>x</a></html>"))
    assert server.list_shelves("1234") == []
