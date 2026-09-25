"""Pin `.claude/skills/check-live-endpoints/SKILL.md` to the code it describes.

That skill is the runbook an agent follows when a tool starts returning empty
or wrong data: it names the command that un-skips the live suite, the exception
each broken surface raises, a `python -c` snippet for re-resolving the GraphQL
config, and the identifiers to edit when that snippet fails. Every one of those
is a hand copy of something in `goodreads_mcp/`, and until this file nothing
read the skill at all -- it could drift arbitrarily far from the code and the
suite would stay green, which is the worst possible failure mode for a document
whose whole job is to be correct in the middle of an incident.

The approach here is to *run* the skill rather than grep it:

* **The live-suite command is parsed, not matched.** The env var name and value
  come out of the skill's own fenced block, and the gate in `tests/e2e` is then
  evaluated under exactly those conditions by re-importing the module. Renaming
  `GOODREADS_LIVE`, or changing the value that un-skips, goes red here and in
  every other file that quotes the command.
* **The symptom table is reproduced.** Each row's symptom is provoked against a
  fabricated response through the real client, so a row that stops being true
  -- an exception renamed, a message reworded, `list_shelves` learning to raise
  instead of returning `[]` -- fails rather than quietly misdirecting whoever
  is reading the table at 2am.
* **The `python -c` snippet is executed.** It is extracted from the fence,
  `shlex`-split, and `exec`'d against a mock transport. Its printed output is
  checked against the expectation the *prose* states, with both sides parsed,
  so swapping `graphql_config`'s `(endpoint, key)` return order -- which no
  other test would notice, since both elements are strings -- fails here.
* **The identifier list is exhaustive in both directions.** The skill tells the
  reader which names to update; that set is computed from the transitive name
  closure of `graphql_config` in `client.py`, so a parser added to the
  discovery path has to be added to the runbook too. This is what caught the
  list omitting `parse_appsync_endpoint` and `APPSYNC_ENDPOINT_RE`, the primary
  path since page-level key discovery landed, while naming
  `APPSYNC_PAIR_RE`, the legacy fallback (fixed in the same change).
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import shlex
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

_ROOT = Path(__file__).resolve().parent.parent
_SKILL_DIR = _ROOT / ".claude" / "skills" / "check-live-endpoints"
_SKILL = _SKILL_DIR / "SKILL.md"
_CLIENT_PY = _ROOT / "goodreads_mcp" / "client.py"
_AGENTS = _ROOT / "AGENTS.md"
_E2E = _ROOT / "tests" / "e2e"

_TEXT = _SKILL.read_text(encoding="utf-8")

# Every file that quotes the live-suite command. The skill is the anchor: its
# parsed command is what these are compared against, so renaming the variable
# in one place is a failure rather than a silent divergence. Asserted to be
# exactly the set of files quoting it below.
_QUOTES_THE_COMMAND = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    ".cursor/rules/goodreads-mcp.mdc",
    ".claude/README.md",
    ".claude/memory/corrections.md",
    ".github/copilot-instructions.md",
    "docs/quality.md",
    "docs/review-rubric.md",
    "docs/risk-tiers.md",
    "prompts/diagnose-parse-regression.md",
)

# Prose that sets the variable in some other form. `README.md` sets it in front
# of a whole-suite run rather than the `tests/e2e` one, which un-skips the live
# tier just the same; it is held to the `NAME=VALUE` part only.
_SETS_THE_VARIABLE = ("README.md",)

# The surfaces the symptom table is allowed to name, each mapped to the
# `## The data surfaces` entry in AGENTS.md it belongs to. A row naming a
# surface that is not one of these -- or an AGENTS.md rename -- goes red.
_TABLE_SURFACES = {
    "HTML pages": "Scraped HTML",
    "AppSync GraphQL": "AppSync GraphQL",
    "page JSON": "Embedded page JSON",
    "page JSON / GraphQL": "Embedded page JSON",
    "shelf RSS": "Shelf RSS",
    "scraped HTML": "Scraped HTML",
}

_NUMBER_WORDS = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}

# Not part of the checkout's prose: a local virtualenv or a dependency tree can
# carry markdown of its own, and it is not this repo's copy of anything.
_IGNORED_DIRS = {".git", "venv", "vendor", "node_modules", "site-packages"}


# ----------------------------------------------------------------- readers


def _frontmatter() -> dict[str, str]:
    """The `---`-delimited YAML header, as flat `key: value` pairs."""
    assert _TEXT.startswith("---\n"), "SKILL.md must open with frontmatter"
    end = _TEXT.index("\n---\n", 3)
    out = {}
    for line in _TEXT[4:end].splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def _body() -> str:
    return _TEXT[_TEXT.index("\n---\n", 3) + 5 :]


def _sections() -> dict[str, str]:
    """`## ` sections of the body, heading -> text up to the next heading."""
    out: dict[str, str] = {}
    heading = ""
    for line in _body().splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            out[heading] = ""
        elif heading:
            out[heading] += line + "\n"
    return out


def _fences(text: str, info: str) -> list[str]:
    """Fenced blocks carrying exactly `info`, bodies only."""
    return re.findall(rf"^```{re.escape(info)}\n(.*?)^```$", text, re.M | re.S)


def _table(text: str) -> list[list[str]]:
    """Rows of the first pipe table in `text`, delimiter row dropped."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set("-: ") and c for c in cells):
            continue
        rows.append(cells)
    return rows


def _live_command() -> tuple[str, str, list[str]]:
    """(env name, env value, argv) from the `Run the live suite` fence.

    The argv is kept whole -- target *and* flags. `-v` is part of what the
    runbook tells the reader to run, and dropping it from the comparison is
    what lets a copy elsewhere quietly diverge.
    """
    blocks = _fences(_sections()["Run the live suite"], "bash")
    assert len(blocks) == 1, "expected exactly one command block under that heading"
    parts = shlex.split(blocks[0].strip())
    assert parts[1] == "pytest", f"expected a pytest invocation, got {parts!r}"
    name, _, value = parts[0].partition("=")
    return name, value, parts[1:]


def _client_tree() -> ast.Module:
    return ast.parse(_CLIENT_PY.read_text(encoding="utf-8"))


def _discovery_names() -> set[str]:
    """Regexes and parsers reachable from `graphql_config` in client.py.

    Walks the transitive closure of module-level names used by the method, then
    keeps the compiled regexes (`*_RE`) and the `parse_*` helpers -- i.e. the
    things an operator edits when discovery stops resolving.
    """
    tree = _client_tree()
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    start = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "graphql_config"
    )
    seen: set[str] = set()
    queue = [start]
    while queue:
        node = queue.pop()
        for used in ast.walk(node):
            if isinstance(used, ast.Name) and used.id not in seen:
                seen.add(used.id)
                if used.id in functions:
                    queue.append(functions[used.id])
    return {n for n in seen if n.endswith("_RE") or n.startswith("parse_")}


# --------------------------------------------------------------- fixtures


_FAKE_KEY = "da2-abcdefghijklmnop"
_FAKE_ENDPOINT = "https://fake123.appsync-api.us-east-1.amazonaws.com/graphql"
_APP_CHUNK = "/_next/static/chunks/pages/_app-0123abcd.js"

_CONFIG_PAGE = (
    '<html><head><script src="' + _APP_CHUNK + '"></script></head><body>'
    '<script id="__NEXT_DATA__" type="application/json">'
    + json.dumps({"props": {"pageProps": {"apiKey": _FAKE_KEY}}})
    + "</script></body></html>"
)
_APP_BUNDLE = '{"endpoint":"' + _FAKE_ENDPOINT + '","shortName":"Prod"}'


def _discovery_handler(seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == client_mod.CONFIG_DISCOVERY_PATH:
            return httpx.Response(200, text=_CONFIG_PAGE)
        if request.url.path == _APP_CHUNK:
            return httpx.Response(200, text=_APP_BUNDLE)
        return httpx.Response(404, text="")

    return handler


@pytest.fixture
def mock_goodreads(monkeypatch):
    """Point every `GoodreadsClient` at a handler, class-wide.

    The skill's snippet constructs its own client, so the transport has to be
    installed on the class rather than on an instance the test holds. It goes on
    the `client` property and not on the `_client` field behind it: `_client` is
    a dataclass field, so `__init__` resets it to `None` on every instance and
    the property quietly builds a real, network-bound client instead.
    """

    def install(handler):
        # follow_redirects matches the real client: a login-gated path is a
        # 302 the client follows to a 200 form, and the check for it reads
        # where the response landed (#91).
        transport = httpx.Client(
            base_url="https://www.goodreads.com",
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        monkeypatch.setattr(
            GoodreadsClient, "client", property(lambda self: transport)
        )

    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    return install


# ------------------------------------------------------------- frontmatter


def test_the_skill_name_is_the_directory_claude_code_loads_it_from():
    assert _frontmatter()["name"] == _SKILL_DIR.name


def test_the_description_says_when_to_reach_for_the_skill():
    """A description with no trigger is a skill that never gets invoked."""
    description = _frontmatter()["description"]
    assert "Use when" in description
    assert description.endswith(".")


def test_the_body_has_the_four_sections_the_runbook_is_built_from():
    assert list(_sections()) == [
        "Run the live suite",
        "Read the failure by surface",
        "Confirm GraphQL config still resolves",
        "After fixing",
    ]


# ----------------------------------------------------------- surface count


def test_the_surface_count_in_the_opening_matches_agents_md():
    """`five` is a hand copy of AGENTS.md's list; a sixth surface breaks it."""
    word = re.search(r"rides (\w+) unofficial Goodreads surfaces", _body()).group(1)
    claimed = _NUMBER_WORDS[word]

    agents = _AGENTS.read_text(encoding="utf-8")
    section = agents.split("## The data surfaces", 1)[1].split("\n## ", 1)[0]
    numbered = re.findall(r"^(\d+)\. \*\*(.+?)\*\*", section, re.M)

    assert [n for n, _ in numbered] == [str(i) for i in range(1, claimed + 1)]
    assert len(numbered) == claimed


# -------------------------------------------------------- the live command


def test_the_live_command_targets_exactly_the_gated_tier():
    """Every module under the documented target has to be behind the variable.

    A target that merely *contains* the live tests -- `tests`, say -- would run
    the offline suite over the network as well, so the path is checked against
    what is gated rather than against what exists.
    """
    name, _, argv = _live_command()
    target = argv[1]
    path = _ROOT / target
    assert path.is_dir(), f"{target} is not a directory"

    modules = sorted(path.rglob("test_*.py"))
    assert modules, f"{target} collects nothing"
    for module in modules:
        source = module.read_text(encoding="utf-8")
        assert "pytestmark" in source and name in source, (
            f"{module.relative_to(_ROOT)} is not gated on {name}"
        )


def test_the_gate_in_the_e2e_suite_reads_the_variable_the_skill_sets(monkeypatch):
    """Evaluate the skip condition under the skill's own env, both ways.

    `pytestmark` is built at import time, so re-importing under each condition
    runs the real gate rather than re-stating it.
    """
    name, value, _ = _live_command()
    module = "tests.e2e.test_smoke_live"

    monkeypatch.delenv(name, raising=False)
    assert importlib.reload(importlib.import_module(module)).pytestmark.args[0] is True

    monkeypatch.setenv(name, value)
    assert importlib.reload(importlib.import_module(module)).pytestmark.args[0] is False

    monkeypatch.setenv(name, value + "0")
    assert importlib.reload(importlib.import_module(module)).pytestmark.args[0] is True


def test_the_gates_reason_tells_the_reader_the_same_thing_the_skill_does():
    name, value, _ = _live_command()
    module = importlib.import_module("tests.e2e.test_smoke_live")
    assert f"{name}={value}" in module.pytestmark.kwargs["reason"]


def test_every_file_quoting_the_live_command_quotes_the_skills_version():
    name, value, argv = _live_command()
    command = " ".join([f"{name}={value}", *argv])

    quoting = {
        str(path.relative_to(_ROOT))
        for path in _ROOT.rglob("*")
        if path.is_file()
        and path.suffix in {".md", ".mdc"}
        and not _IGNORED_DIRS & set(path.relative_to(_ROOT).parts)
        and not any(part.startswith(".venv") for part in path.parts)
        and path != _SKILL
        and _ROOT / "tests" not in path.parents
        and name in path.read_text(encoding="utf-8", errors="ignore")
    }

    expected = set(_QUOTES_THE_COMMAND) | set(_SETS_THE_VARIABLE)
    assert quoting == expected, "update _QUOTES_THE_COMMAND / _SETS_THE_VARIABLE"
    assert not set(_QUOTES_THE_COMMAND) & set(_SETS_THE_VARIABLE)

    # Compared token by token, not as a substring: `... pytest tests` is a
    # prefix of `... pytest tests/e2e -v`, so a substring check would accept a
    # skill that had dropped the path or the flag.
    wanted = command.split()
    for relative in _QUOTES_THE_COMMAND:
        text = (_ROOT / relative).read_text(encoding="utf-8")
        found = re.findall(rf"{re.escape(name)}=\S+ pytest[^\n`]*", text)
        assert found, f"{relative} sets {name} but runs something else"
        for invocation in found:
            assert invocation.split()[: len(wanted)] == wanted, (
                f"{relative} runs `{invocation.strip()}`, not `{command}`"
            )
    for relative in _SETS_THE_VARIABLE:
        text = (_ROOT / relative).read_text(encoding="utf-8")
        assert f"{name}={value} " in text, f"{relative} sets a different value"
        assert command not in text, f"{relative} belongs in _QUOTES_THE_COMMAND"


def test_a_bare_pytest_run_collects_the_offline_suite():
    """"caught by `pytest -q`" only holds if `testpaths` reaches the tests."""
    assert "caught by `pytest -q`" in _sections()["After fixing"]
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'testpaths = ["tests"]' in pyproject


# ------------------------------------------------------- the symptom table


def test_the_symptom_table_has_a_row_per_way_a_surface_can_break():
    rows = _table(_sections()["Read the failure by surface"])
    assert rows[0] == ["symptom", "surface", "what happened"]
    assert len(rows) == 10, "header plus nine symptoms"
    assert all(len(row) == 3 for row in rows)


def test_every_surface_named_in_the_table_is_a_surface_agents_md_lists():
    rows = _table(_sections()["Read the failure by surface"])[1:]
    named = {row[1] for row in rows}
    assert named == set(_TABLE_SURFACES), "update _TABLE_SURFACES"

    agents = _AGENTS.read_text(encoding="utf-8")
    section = agents.split("## The data surfaces", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"^\d+\. \*\*(.+?)\*\*", section, re.M))
    assert set(_TABLE_SURFACES.values()) <= documented


def _symptom(fragment: str) -> str:
    rows = _table(_sections()["Read the failure by surface"])[1:]
    matches = [row for row in rows if fragment in row[0]]
    assert len(matches) == 1, f"expected one row mentioning {fragment!r}"
    return matches[0][0]


def test_the_waf_row_names_the_exception_and_status_the_client_actually_uses(
    mock_goodreads,
):
    symptom = _symptom("WAFChallenge")
    row = [r for r in _table(_sections()["Read the failure by surface"])[1:]
           if r[0] == symptom][0]
    status = int(re.search(r"HTTP (\d+)", row[2]).group(1))

    marker = client_mod.WAF_MARKERS[0]
    mock_goodreads(
        lambda request: httpx.Response(
            status, text=f"<html>{marker}</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(WAFChallenge):
        GoodreadsClient().get("/book/show/1")


def test_the_waf_row_would_be_wrong_for_any_other_status(mock_goodreads):
    """202 is in the table because it is the only status that is a challenge."""
    marker = client_mod.WAF_MARKERS[0]
    mock_goodreads(
        lambda request: httpx.Response(
            200, text=f"<html>{marker}</html>", headers={"content-type": "text/html"}
        )
    )
    assert GoodreadsClient().get("/book/show/1").status_code == 200


def test_the_login_row_names_the_path_and_exception_the_client_actually_uses(
    mock_goodreads,
):
    """#91: a login-gated path is a 302 the client follows to a 200 sign-in
    form, so status alone cannot tell it from content. The row names the path
    the response lands on; provoke exactly that and expect the exception."""
    symptom = _symptom("LoginRequired")
    row = [r for r in _table(_sections()["Read the failure by surface"])[1:]
           if r[0] == symptom][0]
    path = re.search(r"`(/user/[a-z_]+)`", row[2]).group(1)
    assert path == client_mod.SIGN_IN_PATH

    landed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        landed.append(request.url.path)
        if request.url.path == "/review/list/1":
            return httpx.Response(
                302, headers={"location": f"{path}?returnurl=%2Freview%2Flist%2F1"}
            )
        return httpx.Response(200, text="<html><form id='signIn'></form></html>")

    mock_goodreads(handler)
    with pytest.raises(LoginRequired):
        GoodreadsClient().get("/review/list/1")
    assert landed == ["/review/list/1", path], "the redirect was not followed"


def test_the_login_row_would_be_wrong_for_a_page_that_merely_links_sign_in(
    mock_goodreads,
):
    """Every Goodreads page links /user/sign_in in its header. The check is on
    where the response landed, not on the body, or every page would raise."""
    mock_goodreads(
        lambda request: httpx.Response(
            200, text='<html><a href="/user/sign_in">Sign in</a></html>'
        )
    )
    assert GoodreadsClient().get("/review/list/1").status_code == 200


def test_the_login_row_is_true_of_a_private_profile(mock_goodreads):
    """The row's second cause: a private profile is a normal 200 whose
    bookshelves module is replaced by a marker box, and `list_shelves` raises
    the same exception rather than returning `[]`."""
    row = [r for r in _table(_sections()["Read the failure by surface"])[1:]
           if r[0] == _symptom("LoginRequired")][0]
    assert "profile is private" in row[2]

    mock_goodreads(
        lambda request: httpx.Response(
            200, text=f"<html><div {server._PRIVATE_PROFILE_MARKER}></div></html>"
        )
    )
    with pytest.raises(LoginRequired):
        server.list_shelves("1234")


def test_the_graphql_row_names_the_exception_a_dataless_body_raises(mock_goodreads):
    assert "GraphQLError" in _symptom("GraphQLError")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"errors": [{"message": "nope"}]})
        return _discovery_handler([])(request)

    mock_goodreads(handler)
    with pytest.raises(GraphQLError):
        GoodreadsClient().graphql("query { x }")


def test_the_graphql_row_statuses_are_the_ones_that_force_re_discovery(
    mock_goodreads,
):
    """The 401/403 row is the rotation path: each one re-resolves once and retries."""
    statuses = [int(s) for s in re.findall(r"\b(\d{3})\b", _symptom("401/403"))]
    assert statuses, "the row no longer names a status"

    for status in statuses:
        posts: list[int] = []
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                posts.append(len(posts))
                if len(posts) == 1:
                    return httpx.Response(status, json={})
                return httpx.Response(200, json={"data": {"ok": True}})
            return _discovery_handler(seen)(request)

        mock_goodreads(handler)
        assert GoodreadsClient().graphql("query { x }") == {"ok": True}
        assert len(posts) == 2, f"{status} did not trigger a retry"
        # Two discovery rounds: the first config, then the forced re-resolve.
        assert seen.count(client_mod.CONFIG_DISCOVERY_PATH) == 2


def test_a_status_the_row_does_not_name_is_not_retried(mock_goodreads):
    posts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(len(posts))
            return httpx.Response(500, json={})
        return _discovery_handler([])(request)

    mock_goodreads(handler)
    with pytest.raises(httpx.HTTPStatusError):
        GoodreadsClient().graphql("query { x }")
    assert len(posts) == 1


def test_the_next_data_row_quotes_the_error_the_parser_raises():
    symptom = _symptom("__NEXT_DATA__")
    quoted = re.search(r"`ValueError: (.+?)`", symptom).group(1)
    with pytest.raises(ValueError) as excinfo:
        GoodreadsClient.parse_next_data("<html>no blob</html>")
    assert str(excinfo.value).startswith(quoted)


def test_the_rss_row_is_true_of_a_feed_with_no_items():
    assert "RSS returns no items" in _symptom("RSS")
    empty = "<rss><channel><title>shelf</title></channel></rss>"
    assert GoodreadsClient.parse_shelf_rss(empty) == []


def test_the_list_shelves_row_is_true_of_a_page_with_no_shelf_links(monkeypatch):
    """The row promises `[]`, not an exception -- that is what makes it a
    symptom -- and names the page and the link params the tool reads."""
    assert "`list_shelves` returns `[]`" in _symptom("list_shelves")
    row = [r for r in _table(_sections()["Read the failure by surface"])[1:]
           if r[0] == _symptom("list_shelves")][0]
    page = re.search(r"`(/user/show/\{uid\})`", row[2]).group(1)
    params = set(re.findall(r"`(\w+)=`", row[2]))
    assert params == {"shelf", "tag"}

    fetched: list[str] = []

    def get(url, **kw):
        fetched.append(url)
        return httpx.Response(200, text="<html></html>")

    monkeypatch.setattr(server.gr, "get", get)
    assert server.list_shelves("1234") == []
    assert fetched == [page.replace("{uid}", "1234")]


def test_the_rate_limiting_note_names_the_statuses_the_client_backs_off_on():
    """`429/503` in the prose against the tuple `_request` compares against."""
    prose = _body().split("## Read the failure", 1)[0]
    noted = {int(s) for s in re.findall(r"\b(\d{3})\b", prose)}

    tree = _client_tree()
    request = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_request"
    )
    literals = {
        elt.value
        for node in ast.walk(request)
        if isinstance(node, ast.Compare)
        and isinstance(node.ops[0], ast.NotIn)
        and isinstance(node.comparators[0], ast.Tuple)
        for elt in node.comparators[0].elts
    }
    assert noted == literals


def test_exhausted_retries_surface_as_the_error_the_note_describes(mock_goodreads):
    attempts: list[int] = []
    mock_goodreads(
        lambda request: (attempts.append(1), httpx.Response(429, text=""))[1]
    )
    gr = GoodreadsClient()
    with pytest.raises(httpx.HTTPStatusError):
        gr.get("/anything")
    assert len(attempts) == gr.max_retries + 1


# --------------------------------------------------- the GraphQL config check


def _snippet() -> str:
    blocks = _fences(_sections()["Confirm GraphQL config still resolves"], "bash")
    assert len(blocks) == 1
    parts = shlex.split(blocks[0].strip())
    assert parts[0] == "python" and parts[1] == "-c", parts[:2]
    assert len(parts) == 3, "the snippet must be a single -c argument"
    return parts[2]


def _expectation() -> tuple[str, str, str]:
    """(endpoint fragment, endpoint suffix, key prefix) from the prose."""
    section = _sections()["Confirm GraphQL config still resolves"]
    match = re.search(
        r"Expect an `(.+?)` endpoint ending in `(.+?)` and a key starting\s+`(.+?)`",
        section,
    )
    assert match, "the expectation sentence has been reworded"
    return match.group(1), match.group(2), match.group(3)


def test_the_snippet_is_valid_python():
    compile(_snippet(), "<skill>", "exec")


def test_the_snippet_prints_what_the_prose_says_it_will(mock_goodreads, capsys):
    """Run the documented command; check its output against the documented claim.

    This is the assertion that survives a refactor of `graphql_config`: both
    elements of its return are strings, so swapping them breaks only the
    reader of this snippet, and nothing else in the suite unpacks it by shape.
    """
    fragment, suffix, prefix = _expectation()
    mock_goodreads(_discovery_handler([]))

    exec(compile(_snippet(), "<skill>", "exec"), {"__name__": "__main__"})

    printed = capsys.readouterr().out.splitlines()
    assert len(printed) == 2, printed
    endpoint, key = printed
    assert fragment in endpoint
    assert endpoint.endswith(suffix)
    assert endpoint == _FAKE_ENDPOINT
    assert key.startswith(prefix)
    assert key == _FAKE_KEY[:8] + "..."


def test_the_snippet_forces_a_fresh_resolve_rather_than_reading_the_cache(
    mock_goodreads,
):
    """`force=True` is the point of the check -- a cached value proves nothing."""
    assert "force=True" in _snippet()
    seen: list[str] = []
    mock_goodreads(_discovery_handler(seen))

    gr = GoodreadsClient()
    gr.graphql_config()
    gr.graphql_config()
    assert seen.count(client_mod.CONFIG_DISCOVERY_PATH) == 1
    gr.graphql_config(force=True)
    assert seen.count(client_mod.CONFIG_DISCOVERY_PATH) == 2


def test_the_documented_shapes_are_the_shapes_the_regexes_accept():
    fragment, suffix, prefix = _expectation()
    for pattern in (client_mod.APPSYNC_PAIR_RE, client_mod.APPSYNC_ENDPOINT_RE):
        assert fragment in pattern.pattern
        assert suffix in pattern.pattern
    assert client_mod.APPSYNC_KEY_RE.pattern.startswith(prefix)
    assert client_mod.APPSYNC_KEY_RE.fullmatch(_FAKE_KEY)


def test_the_names_to_update_are_exactly_the_discovery_path(mock_goodreads):
    """The runbook's identifier list, against the closure of `graphql_config`.

    Exhaustive both ways on purpose: a name dropped from the skill leaves an
    operator editing the wrong file, and a parser added to the discovery path
    without being named here is one they will not think to look at.
    """
    section = _sections()["Confirm GraphQL config still resolves"]
    # Only the enumeration, not the sentences around it: the prose after it
    # singles one name out again, and a name mentioned there is not a list entry.
    listed = section.split("needs updating:", 1)[1].split("Start with", 1)[0]
    named = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", listed))
    assert named == _discovery_names()


def test_every_name_the_runbook_lists_exists_in_the_module():
    for name in _discovery_names():
        assert hasattr(client_mod, name), name


def test_the_runbook_still_refuses_the_shortcut_it_was_written_against():
    """The skill's own closing rule, and the code holding to it.

    `client.py` must not carry a literal key or endpoint outside the patterns
    that recognise one -- the correction this whole surface exists to prevent.
    """
    section = " ".join(_sections()["Confirm GraphQL config still resolves"].split())
    assert "never respond by hardcoding a key or endpoint" in section.lower()

    prefix = _expectation()[2]
    for node in ast.walk(_client_tree()):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id.endswith("_RE"):
            continue
        value = node.value.value
        if isinstance(value, str):
            assert prefix not in value
            assert "appsync-api" not in value
