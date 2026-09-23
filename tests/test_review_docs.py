"""The review rubric and the risk tiers, read as subjects rather than sources.

`docs/review-rubric.md` is the checklist a reviewer (human or agent) walks
through before approving a PR here, and `docs/risk-tiers.md` says how much
evidence each kind of change needs. Five tests already open one or both, and
every one reads them as a *source*: `test_coverage_thresholds.py` takes the
`55`, `test_check_live_endpoints_skill.py` takes the live-suite command,
`test_release_workflow.py` takes the version-sync sentence,
`test_ai_fix_workflow.py` and `test_agent_permissions.py` take one sentence
each. None of them checked what the two pages say about the code — the caps
they name, the two hand-paginating tools, the paging contract, the files that
must never be committed, the labels, and the tool registry both pages tell a
reviewer to keep in step.

That last one was unchecked everywhere. Rubric §6 and Tier 2 both require a
new tool to be "added to the `README.md` table and the `server.py` docstring
list", and `AGENTS.md` counts "the 12 `@mcp.tool` functions" — three copies
of what the decorators say, and no test compared any of them to the
decorators. Both lists also say *which surface* every tool rides ("GraphQL" or
not), which is exactly the fact Tier 1 turns on.

The rubric had drifted in the way a checklist does: by stating a rule more
broadly than the code keeps it. §4 said "`returned` / `has_more` present on
paginated results", but `get_reviews` pages through `nextPageToken` by hand
(§3 says so) and has never returned `has_more` — it returns `returned` and
`total_text_reviews`. A reviewer holding a new hand-paginated tool to the
rubric would have asked for a key the one existing precedent does not carry,
or taken `get_reviews` for a bug. The line now names the helper and the
exception, and `test_the_paging_contract_names_every_tool_that_breaks_it`
derives the exception set from the return dicts, so a fix to `get_reviews`
turns it red until the rubric stops excusing it.

Everything is checked against the code, not against a second copy of the
claim, and the backtick vocabulary of both pages is partitioned exhaustively
(`_kind`) so a new token must be classified before it can land unchecked.
"""

from __future__ import annotations

import ast
import fnmatch
import json
from pathlib import Path
import re
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_RUBRIC = "docs/review-rubric.md"
_TIERS = "docs/risk-tiers.md"
_DOCS = (_RUBRIC, _TIERS)

_SERVER_PY = _ROOT / "goodreads_mcp" / "server.py"
_CLIENT_PY = _ROOT / "goodreads_mcp" / "client.py"
_README = _ROOT / "README.md"
_AGENTS = _ROOT / "AGENTS.md"
_LABELER = _ROOT / ".github" / "labeler.yml"
_SETTINGS = _ROOT / ".claude" / "settings.json"

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15,
}


def _text(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _squashed(rel: str) -> str:
    """Markdown wraps; a phrase can break across lines."""
    return re.sub(r"\s+", " ", _text(rel))


def _section(rel: str, heading: str) -> str:
    """The body of the `##` section whose heading starts with `heading`."""
    text = _text(rel)
    match = re.search(rf"^## {re.escape(heading)}.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"{rel} has no section headed {heading!r}"
    return re.sub(r"\s+", " ", match.group(1))


def _backticks(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", text)


# ============================================================== server.py AST

_TREE = ast.parse(_SERVER_PY.read_text(encoding="utf-8"))
_FUNCS = {n.name: n for n in _TREE.body if isinstance(n, ast.FunctionDef)}
_TOOLS = {
    name: f
    for name, f in _FUNCS.items()
    if any("mcp.tool" in ast.unparse(d) for d in f.decorator_list)
}
_CONSTANTS = {
    t.id: node.value.value
    for node in _TREE.body
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
    for t in node.targets
    if isinstance(t, ast.Name)
}
_HELPER = "_paginated_graphql_edges"


def _called(func: ast.AST) -> set[str]:
    """Plain names and `.attr` method names this function calls directly."""
    out: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                out.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                out.add("." + node.func.attr)
    return out


def _reaches(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
    """Everything `name` calls, following calls into other server.py functions."""
    if name in seen:
        return set()
    direct = _called(_FUNCS[name])
    out = set(direct)
    for callee in direct & _FUNCS.keys():
        out |= _reaches(callee, seen | {name})
    return out


def _uses_graphql(tool: str) -> bool:
    return ".graphql" in _reaches(tool)


def _return_keys(func: ast.AST) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return keys


def _names_in(func: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(func) if isinstance(n, ast.Name)}


def _names_reached(tool: str) -> set[str]:
    """Names read by `tool` or by any server.py function it reaches."""
    funcs = [_FUNCS[tool]] + [_FUNCS[c] for c in _reaches(tool) if c in _FUNCS]
    return set().union(*(_names_in(f) for f in funcs))


_GRAPHQL_TOOLS = {t for t in _TOOLS if _uses_graphql(t)}
_HELPER_TOOLS = {t for t in _TOOLS if _HELPER in _reaches(t)}
_HAND_PAGINATORS = _GRAPHQL_TOOLS - _HELPER_TOOLS


def test_the_server_has_tools_to_check():
    assert len(_TOOLS) >= 10, sorted(_TOOLS)
    assert _GRAPHQL_TOOLS and _HELPER_TOOLS and _HAND_PAGINATORS


# ======================================================== the tool registry

def _readme_tool_rows() -> dict[str, str]:
    """`| `tool` | description |` rows of the README's tool table."""
    rows = {}
    for line in _README.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| `(\w+)` \| (.+) \|$", line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def _docstring_tool_rows() -> dict[str, str]:
    """The indented `name   surface` list under "Tools" in server.py's docstring."""
    doc = ast.get_docstring(_TREE) or ""
    block = re.search(r"^Tools\b.*?:\n\n(.*?)\n\n", doc, re.M | re.S)
    assert block, "server.py's docstring no longer has a Tools list"
    rows = {}
    for line in block.group(1).splitlines():
        match = re.match(r"^\s+(\w+)\s{2,}(.+)$", line)
        assert match, f"unparsed line in server.py's Tools list: {line!r}"
        rows[match.group(1)] = match.group(2)
    return rows


@pytest.mark.parametrize(
    "where, rows",
    [("README.md tool table", _readme_tool_rows), ("server.py docstring", _docstring_tool_rows)],
)
def test_every_tool_list_names_exactly_the_decorated_tools(where, rows):
    """Rubric §6 / Tier 2: "the `README.md` table and the `server.py` docstring list"."""
    listed = set(rows())
    assert listed - _TOOLS.keys() == set(), f"{where} lists tools that do not exist"
    assert _TOOLS.keys() - listed == set(), f"{where} is missing tools"


@pytest.mark.parametrize(
    "where, rows, graphql",
    [
        ("README.md tool table", _readme_tool_rows, re.compile(r"^GraphQL\b")),
        ("server.py docstring", _docstring_tool_rows, re.compile(r"\bGraphQL\b")),
    ],
)
def test_every_tool_list_says_which_tools_ride_graphql(where, rows, graphql):
    """The surface a tool rides decides its tier; both lists state it per tool.

    A tool is GraphQL when it reaches `gr.graphql`, directly or through another
    server.py function. Checked both ways, so neither a stale label nor a
    missing one passes.
    """
    labelled = {name for name, desc in rows().items() if graphql.search(desc)}
    assert labelled == _GRAPHQL_TOOLS, (
        f"{where}: labelled GraphQL {sorted(labelled)}, "
        f"call GraphQL {sorted(_GRAPHQL_TOOLS)}"
    )


def test_agents_md_counts_the_decorated_tools():
    match = re.search(r"the (\w+) `@mcp\.tool` functions", _AGENTS.read_text(encoding="utf-8"))
    assert match, "AGENTS.md no longer counts the @mcp.tool functions"
    stated = match.group(1)
    count = int(stated) if stated.isdigit() else _NUMBER_WORDS[stated.lower()]
    assert count == len(_TOOLS), f"AGENTS.md says {count}, server.py decorates {len(_TOOLS)}"


# ================================================ rubric §3 — politeness

def _hand_pagination_sentence() -> str:
    section = _section(_RUBRIC, "3.")
    match = re.search(r"(\w+) tools legitimately paginate by hand.*?(?=`compare_books` likewise)", section)
    assert match, "rubric §3 no longer names the tools that paginate by hand"
    return match.group(0)


def test_the_rubric_names_exactly_the_tools_that_paginate_by_hand():
    """"Two tools legitimately paginate by hand" — the number and the names.

    Hand-paginators are GraphQL tools with a paging loop that do not go
    through the helper; derived from the AST, compared both ways.
    """
    sentence = _hand_pagination_sentence()
    named = {t for t in _backticks(sentence) if t in _TOOLS}
    assert named == _HAND_PAGINATORS, f"rubric names {sorted(named)}, code has {sorted(_HAND_PAGINATORS)}"
    word = sentence.split()[0].lower()
    assert _NUMBER_WORDS[word] == len(_HAND_PAGINATORS)


def test_the_helper_and_its_caps_are_what_the_rubric_says():
    """"A new standard discovery connection uses `_paginated_graphql_edges` and
    respects the caps (`_MAX_DISCOVERY`, `_DISCOVERY_PAGE_SIZE`)"."""
    assert _HELPER_TOOLS, "no tool uses the pagination helper any more"
    section = _section(_RUBRIC, "3.")
    caps = {t for t in _backticks(section) if t.startswith("_MAX_DISCOVERY") or t == "_DISCOVERY_PAGE_SIZE"}
    assert caps == {"_MAX_DISCOVERY", "_DISCOVERY_PAGE_SIZE"}, caps
    for tool in _HELPER_TOOLS:
        unread = caps - _names_reached(tool)
        assert not unread, f"{tool} no longer respects {sorted(unread)}"


@pytest.mark.parametrize(
    "tool, caps",
    [
        ("popular_books", {"_MAX_POPULAR", "_POPULAR_PAGE_SIZE"}),
        ("compare_books", {"_MAX_COMPARE"}),
    ],
)
def test_each_tool_the_rubric_gives_its_own_cap_enforces_it(tool, caps):
    section = _section(_RUBRIC, "3.")
    for cap in caps:
        assert re.search(rf"`{tool}`[^.]*`{cap}`", section), f"rubric §3 no longer ties {cap} to {tool}"
        assert cap in _names_reached(tool), f"{tool} no longer reads {cap}"


def test_popular_books_passes_after_and_limit_as_top_level_variables():
    """"`popular_books` (passes `after` and `limit` as top-level variables ...)"."""
    for node in ast.walk(_TOOLS["popular_books"]):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "graphql"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Dict)
        ):
            keys = {k.value for k in node.args[1].keys if isinstance(k, ast.Constant)}
            assert {"after", "limit"} <= keys and "pagination" not in keys, keys
            return
    pytest.fail("popular_books no longer calls gr.graphql with a literal variables dict")


# ============================================ rubric §4 — the tool contract

def test_the_paging_contract_names_every_tool_that_breaks_it():
    """Rubric §4: which paging tools carry `has_more`, and which one does not.

    Every paging tool returns `returned`. `has_more` is carried by the helper
    tools and `popular_books`; the tools that page but do not carry it are
    derived from the return dicts and must be exactly the ones the rubric
    names on that line — so `get_reviews` gaining `has_more` turns this red
    until the rubric stops excusing it, and a new tool dropping it turns this
    red until someone decides.
    """
    paging = _HELPER_TOOLS | _HAND_PAGINATORS
    for tool in paging:
        assert "returned" in _return_keys(_TOOLS[tool]), f"{tool} does not return 'returned'"
    missing = {t for t in paging if "has_more" not in _return_keys(_TOOLS[t])}

    line = next(
        (ln for ln in _section(_RUBRIC, "4.").split("- [ ]") if "`has_more`" in ln), None
    )
    assert line, "rubric §4 no longer states the has_more contract"
    rule, _, exception = line.partition(". ")
    assert "instead" in exception, "rubric §4 no longer names the tools without has_more"
    carriers = {t for t in _backticks(rule) if t in _TOOLS}
    excused = {t for t in _backticks(exception) if t in _TOOLS}
    assert excused == missing, f"rubric §4 excuses {sorted(excused)}; code lacks has_more in {sorted(missing)}"
    assert _HELPER_TOOLS | carriers == paging - missing, (
        f"rubric §4 says has_more is on the helper tools and {sorted(carriers)}"
    )
    assert f"`{_HELPER}`" in rule, "rubric §4 no longer scopes has_more to the helper"
    stated = {t for t in _backticks(exception) if t in _all_return_keys()} - {"returned", "has_more"}
    for tool in excused:
        wrong = stated - _return_keys(_TOOLS[tool])
        assert not wrong, f"rubric §4 says {tool} returns {sorted(wrong)}; it does not"


def test_every_tool_is_annotated_read_only():
    """Rubric §4: "`@mcp.tool(annotations=_READ_ONLY)` on new tools"."""
    for name, func in _TOOLS.items():
        assert any(ast.unparse(d) == "mcp.tool(annotations=_READ_ONLY)" for d in func.decorator_list), name


# ============================================== rubric §6 — housekeeping

def _generated_patterns() -> list[str]:
    section = _section(_RUBRIC, "6.")
    match = re.search(r"No generated files committed \(([^)]*)\)", section)
    assert match, "rubric §6 no longer lists the generated files"
    patterns = _backticks(match.group(1))
    assert len(patterns) >= 3, patterns
    return patterns


def _probe_path(pattern: str) -> str:
    """A concrete path the pattern should cover: `*.mcpb` → `x.mcpb`, `.venv/` → `.venv/x`."""
    path = pattern.replace("*", "x")
    return path + "x" if path.endswith("/") else path


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=_ROOT, capture_output=True, text=True)


def test_every_generated_file_the_rubric_names_is_gitignored():
    for pattern in _generated_patterns():
        result = _git("check-ignore", "-q", "--no-index", _probe_path(pattern))
        assert result.returncode == 0, f"{pattern} (rubric §6) is not ignored by .gitignore"


def test_no_generated_file_the_rubric_names_is_tracked():
    tracked = _git("ls-files").stdout.splitlines()
    assert tracked, "git ls-files returned nothing"
    for pattern in _generated_patterns():
        glob = pattern + "*" if pattern.endswith("/") else pattern
        hits = [p for p in tracked if fnmatch.fnmatch(p, glob) or fnmatch.fnmatch(Path(p).name, glob)]
        assert not hits, f"{pattern} is committed: {hits}"


def test_the_manifest_launches_with_uv_run():
    """Rubric §6: "`manifest.json` launches with `uv run`"."""
    config = json.loads(_text("manifest.json"))["server"]["mcp_config"]
    assert [config["command"], *config["args"]][:2] == ["uv", "run"], config


# ============================================ risk-tiers — labels, hooks

def _labeler_labels() -> set[str]:
    return set(re.findall(r"^([\w-]+):\s*$", _LABELER.read_text(encoding="utf-8"), re.M))


def test_every_label_the_tiers_name_is_applied_by_the_labeler():
    section = _section(_TIERS, "Applying this")
    named = {t for t in _backticks(section) if re.fullmatch(r"[a-z][a-z-]*", t)}
    assert {"client", "server", "live-tests", "ci", "docs"} <= named, named
    assert named <= _labeler_labels(), f"not in .github/labeler.yml: {sorted(named - _labeler_labels())}"


def test_the_guard_the_tiers_describe_is_the_pretooluse_hook():
    """"`.claude/hooks/guard-bash.py` is the `PreToolUse` guard"."""
    squashed = _squashed(_TIERS)
    assert "`.claude/hooks/guard-bash.py` is the `PreToolUse` guard" in squashed
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    commands = [
        h["command"]
        for entry in settings["hooks"]["PreToolUse"]
        for h in entry["hooks"]
    ]
    assert any(".claude/hooks/guard-bash.py" in c for c in commands), commands
    assert "Read(./.env)" in settings["permissions"]["deny"]


# ================================================ the backtick vocabulary

_TRACKED = None


def _tracked() -> list[str]:
    global _TRACKED
    if _TRACKED is None:
        _TRACKED = _git("ls-files").stdout.splitlines()
    return _TRACKED


def _is_tracked_path(token: str) -> bool:
    """A committed path, a glob matching one, or a bare basename in the package."""
    token = token.rstrip("/")
    if token.endswith("/**"):
        return any(p.startswith(token[:-2]) for p in _tracked())
    return (
        token in _tracked()
        or any(p.startswith(token + "/") for p in _tracked())
        or f"goodreads_mcp/{token}" in _tracked()
    )


def _is_server_symbol(token: str) -> bool:
    if token.startswith("client."):
        return re.search(rf"^{re.escape(token[7:])}\b", _CLIENT_PY.read_text(encoding="utf-8"), re.M) is not None
    return token in _FUNCS or token in _CONSTANTS


def _is_cap_glob(token: str) -> bool:
    """`_MAX_*` or a suffix like `_PAGE_SIZE`: matches constants without being one."""
    pattern = token if "*" in token else "*" + token
    return (
        token.startswith("_")
        and token not in _CONSTANTS
        and any(fnmatch.fnmatch(c, pattern) for c in _CONSTANTS)
    )


def _all_return_keys() -> set[str]:
    return set().union(*(_return_keys(f) for f in _FUNCS.values()))


# Tokens that are neither a path nor a symbol. Each says where it is true;
# `test_every_literal_is_true_where_it_says` checks that.
_LITERALS = {
    ".xml": _SERVER_PY,
    "__NEXT_DATA__": _SERVER_PY,
    "raise_for_status()": _CLIENT_PY,
    "PaginationInput": _SERVER_PY,
    "PageInfo": _SERVER_PY,  # the `pageInfo` field, matched without case
    "after": _SERVER_PY,
    "limit": _SERVER_PY,
    "PreToolUse": _SETTINGS,
    "Read(./.env)": _SETTINGS,
    "11870085-the-fault-in-our-stars": _ROOT / "tests" / "e2e" / "test_smoke_live.py",
    "@mcp.tool": _SERVER_PY,
    "@mcp.tool(annotations=_READ_ONLY)": _SERVER_PY,
}
# Commands; each is checked where it runs, not here.
_COMMANDS = {
    "GOODREADS_LIVE=1 pytest tests/e2e -v": "test_check_live_endpoints_skill.py",
    "pytest -q": "ci.yml runs pytest",
    "uv run": "test_the_manifest_launches_with_uv_run",
    "mcpb pack": "release.yml",
}


def _kind(token: str) -> str | None:
    if token in _COMMANDS:
        return "command"
    if token in _LITERALS:
        return "literal"
    if token in _TOOLS:
        return "tool"
    if token in _labeler_labels():
        return "label"
    if _is_server_symbol(token):
        return "symbol"
    if token in _generated_patterns():
        return "generated"
    if token in _all_return_keys():
        return "result_key"
    if _is_tracked_path(token):
        return "path"
    if _is_cap_glob(token):
        return "cap_glob"
    return None


@pytest.mark.parametrize("doc", _DOCS)
def test_every_backticked_token_is_a_claim_of_a_known_kind(doc):
    """Partition the vocabulary; a new token must be classified before it lands."""
    unknown = sorted({t for t in _backticks(_text(doc)) if _kind(t) is None})
    assert not unknown, f"{doc} backticks tokens no test checks: {unknown}"


def test_every_literal_is_true_where_it_says():
    used = set().union(*(_backticks(_text(d)) for d in _DOCS))
    assert set(_LITERALS) <= used, f"stale exemptions: {sorted(set(_LITERALS) - used)}"
    assert set(_COMMANDS) <= used, f"stale commands: {sorted(set(_COMMANDS) - used)}"
    for literal, home in _LITERALS.items():
        text = home.read_text(encoding="utf-8")
        needle = literal.lower() if literal == "PageInfo" else literal
        haystack = text.lower() if literal == "PageInfo" else text
        assert needle in haystack, f"{literal!r} is not in {home.relative_to(_ROOT)}"


def test_every_cap_glob_matches_a_constant_and_every_cap_is_covered():
    """Tier 2 names "`_MAX_*` / `_PAGE_SIZE` caps"; every cap in server.py is one of those."""
    globs = [t for t in _backticks(_text(_TIERS)) if _kind(t) == "cap_glob"]
    assert globs, "risk-tiers no longer names the caps"
    patterns = [g if "*" in g else "*" + g for g in globs]
    for pattern in patterns:
        assert any(fnmatch.fnmatch(c, pattern) for c in _CONSTANTS), f"{pattern} matches no constant"
    caps = {c for c in _CONSTANTS if re.fullmatch(r"_[A-Z_]+", c) and isinstance(_CONSTANTS[c], int)}
    assert caps, "server.py has no integer caps"
    uncovered = {c for c in caps if not any(fnmatch.fnmatch(c, p) for p in patterns)}
    assert not uncovered, f"caps Tier 2 does not cover: {sorted(uncovered)}"
