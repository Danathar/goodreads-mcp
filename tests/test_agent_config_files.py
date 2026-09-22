"""The two always-on agent rule files, read as subjects rather than sources.

`.github/copilot-instructions.md` and `.cursor/rules/goodreads-mcp.mdc` are
loaded automatically by Copilot and Cursor, so whatever they say is what an
agent believes about this repository before it reads a line of code. Both are
hand-written condensations of `AGENTS.md` — AGENTS.md says so itself, in its
third line: "Other agent config files ... point here — edit this file, not the
copies."

Two tests already open these files, and both read them as *sources* for
something defined elsewhere: `test_coverage_thresholds.py` pulls the `55` out
of the copilot file and compares it to `--cov-fail-under` in CI, and
`test_check_live_endpoints_skill.py` pulls the live-suite command out of both
and compares it to the skill's. Nothing checked either file's own technical
claims — that `get_book` fetches the `.xml` path, that `list_shelves` regexes
`shelf=` and `tag=`, that the WAF challenge is HTTP 202, that backoff covers
429/503, that `client.graphql_config` exists and nothing hardcodes what it
resolves. Forty-nine lines of standing instruction, stating a dozen facts
about the code, none of them checked.

They had already drifted, in the way a hand-copied summary does: by dropping a
qualifier. AGENTS.md states the discovery-tool convention and then immediately
carves out the exceptions — "`popular_books` and `compare_books` have their own
tighter caps — check the constants near the top of `server.py` before
assuming." The Cursor copy kept the rule and dropped the carve-out, so it read
as a blanket "cap `limit` at 100" while `_MAX_POPULAR` is 50 and
`_MAX_COMPARE` is 10. An agent trusting it would have raised a cap the source
file exists to protect. `test_a_stated_cap_names_every_tool_that_does_not_obey_it`
derives the exception set from the AST — a tool is an exception when the
`_MAX_*` constant it enforces has a different *value* from `_MAX_DISCOVERY` —
so adding a twelfth tool with a tighter cap puts it on the list and turns every
doc that states the number red until it is named.

The claims are checked against the code, never against a second copy of the
claim: status codes come out of the comparison that tests them, the profile
path out of `list_shelves`' own f-string, the cap set out of the constants.
And the backtick vocabulary of both files is partitioned exhaustively below
(`_CLAIMS`): a token added to either file belongs to one of the five kinds or
these tests fail, which is what stops a new unchecked claim from arriving
quietly.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_AGENTS = _ROOT / "AGENTS.md"
_COPILOT = ".github/copilot-instructions.md"
_CURSOR = ".cursor/rules/goodreads-mcp.mdc"
_DERIVED = (_COPILOT, _CURSOR)

_PKG = _ROOT / "goodreads_mcp"
_SERVER_PY = _PKG / "server.py"
_CLIENT_PY = _PKG / "client.py"


def _text(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _squashed(rel: str) -> str:
    """Markdown wraps; a phrase spanning a line break is still the phrase."""
    return re.sub(r"\s+", " ", _text(rel))


def _ticks(rel: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", _text(rel)))


# --------------------------------------------------------------- the claims
#
# Every backticked token in either derived file, partitioned by what kind of
# claim it makes. Asserted exhaustive in both directions by
# test_every_backticked_token_is_a_classified_claim, so a new token in either
# file has to be classified — and therefore checked — before the suite passes.

# Names that must resolve in the package. A dotted name is module.attribute.
_SYMBOLS = (
    "client.graphql_config",
    "_paginated_graphql_edges",
    "get_book",
    "list_shelves",
    "popular_books",
    "compare_books",
)

# Keys that must appear in a result dict built by server.py.
_RESULT_KEYS = ("returned", "has_more", "url")

# Names that must be a parameter of at least one @mcp.tool.
_TOOL_PARAMS = ("limit",)

# Paths that must exist in the checkout.
_TRACKED_FILES = ("pyproject.toml", "manifest.json")

# Modules of the package, named without their directory.
_PACKAGE_FILES = ("server.py",)

# Literal fragments that must appear in the package source. `/user/show/{uid}`
# is checked separately, against list_shelves' own f-string.
_SOURCE_LITERALS = (".xml", "__NEXT_DATA__", "shelf=", "tag=", "/user/show/{uid}")

# Commands. Both are compared to the skill's copy by
# test_check_live_endpoints_skill.py, which owns that check; listed here only
# so the exhaustiveness assertion below stays honest.
_COMMANDS = ("pytest -q", "GOODREADS_LIVE=1 pytest tests/e2e -v")

_CLAIMS = {
    "symbol": _SYMBOLS,
    "result_key": _RESULT_KEYS,
    "tool_param": _TOOL_PARAMS,
    "tracked_file": _TRACKED_FILES,
    "package_file": _PACKAGE_FILES,
    "source_literal": _SOURCE_LITERALS,
    "command": _COMMANDS,
}


# ------------------------------------------------------------------ readers


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _tools() -> dict[str, ast.FunctionDef]:
    """The `@mcp.tool`-decorated functions in server.py, by name."""
    out: dict[str, ast.FunctionDef] = {}
    for node in _module(_SERVER_PY).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                out[node.name] = node
    return out


def _module_names(path: Path) -> set[str]:
    """Top-level defs, classes and assignments in one module."""
    names: set[str] = set()
    for node in _module(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _attributes(path: Path) -> set[str]:
    """Methods and fields declared on any class in one module."""
    out: set[str] = set()
    for node in ast.walk(_module(path)):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(stmt.name)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                out.add(stmt.target.id)
    return out


def _skeleton(node: ast.AST) -> str | None:
    """An f-string as its literal shape: f"/user/show/{uid}" -> /user/show/{uid}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.JoinedStr):
        return None
    parts = []
    for piece in node.values:
        if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
            parts.append(piece.value)
        elif isinstance(piece, ast.FormattedValue):
            inner = piece.value
            parts.append("{%s}" % (inner.id if isinstance(inner, ast.Name) else "…"))
    return "".join(parts)


def _strings_in(node: ast.AST) -> set[str]:
    out = set()
    for sub in ast.walk(node):
        skel = _skeleton(sub)
        if skel is not None:
            out.add(skel)
    return out


def _strings_reachable_from(name: str) -> set[str]:
    """String literals in a tool's body plus those of the helpers it calls.

    `get_book` states the `.xml` rule by delegating to `_fetch_book_apollo`;
    following one level of module-local calls keeps the check on the behaviour
    rather than on which function currently happens to hold the literal.
    """
    helpers = {
        n.name: n
        for n in _module(_SERVER_PY).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    root = helpers[name]
    out = _strings_in(root)
    for callee in _calls_in(root) & helpers.keys():
        if callee != name:
            out |= _strings_in(helpers[callee])
    return out


def _int_constants() -> dict[str, int]:
    """Module-level `_NAME = <int>` in server.py."""
    out: dict[str, int] = {}
    for node in _module(_SERVER_PY).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, int) and not isinstance(node.value.value, bool):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = node.value.value
    return out


def _status_codes_in(func_name: str, path: Path) -> set[int]:
    """Every literal status code a named function compares `status_code` against."""
    found: set[int] = set()
    for node in ast.walk(_module(path)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Compare):
                continue
            left = sub.left
            if not (isinstance(left, ast.Attribute) and left.attr == "status_code"):
                continue
            for comparator in sub.comparators:
                operands = (
                    comparator.elts
                    if isinstance(comparator, (ast.Tuple, ast.Set, ast.List))
                    else [comparator]
                )
                for operand in operands:
                    if isinstance(operand, ast.Constant) and isinstance(operand.value, int):
                        found.add(operand.value)
    return found


def _calls_in(func: ast.AST) -> set[str]:
    """Names of functions/methods called anywhere inside a function body."""
    out: set[str] = set()
    for sub in ast.walk(func):
        if isinstance(sub, ast.Call):
            target = sub.func
            if isinstance(target, ast.Name):
                out.add(target.id)
            elif isinstance(target, ast.Attribute):
                out.add(target.attr)
    return out


def _caps_by_tool() -> dict[str, set[str]]:
    """Each tool mapped to the `_MAX_*` constants its own body enforces."""
    maxes = {n for n in _int_constants() if n.startswith("_MAX_")}
    out: dict[str, set[str]] = {}
    for name, func in _tools().items():
        used = {s.id for s in ast.walk(func) if isinstance(s, ast.Name)} & maxes
        # A tool that delegates its clamp to the shared validator enforces
        # whatever that validator enforces.
        if "_validate_discovery_limit" in _calls_in(func):
            validator = next(
                n
                for n in _module(_SERVER_PY).body
                if isinstance(n, ast.FunctionDef) and n.name == "_validate_discovery_limit"
            )
            used |= {s.id for s in ast.walk(validator) if isinstance(s, ast.Name)} & maxes
        if used:
            out[name] = used
    return out


# ------------------------------------------------------- the files themselves


@pytest.mark.parametrize("rel", _DERIVED)
def test_each_derived_file_points_at_agents_md(rel: str) -> None:
    """Both files are condensations; each must link the file it condenses."""
    target = re.search(r"\[AGENTS\.md\]\((?:mdc:)?([^)]+)\)", _text(rel))
    assert target, f"{rel} does not link AGENTS.md"
    # Cursor resolves `mdc:` links from the repository root; Markdown resolves
    # a plain relative link from the file's own directory.
    base = _ROOT if "mdc:" in _text(rel) else (_ROOT / rel).parent
    assert (base / target.group(1)).resolve() == _AGENTS.resolve()


def test_agents_md_names_exactly_the_files_that_condense_it() -> None:
    """Both directions: AGENTS.md's list of copies, and the copies themselves."""
    named = set(re.findall(r"`([^`]+)`", _squashed("AGENTS.md").split("edit this file")[0]))
    # `.cursor/rules/` is named as the directory; the file inside it is the copy.
    named = {n.rstrip("/") for n in named if n.endswith((".md", "/"))}
    expected = {"CLAUDE.md", ".github/copilot-instructions.md", ".cursor/rules"}
    assert named == expected, f"AGENTS.md's list of copies changed: {named}"
    for rel in _DERIVED:
        assert "AGENTS.md" in _text(rel), f"{rel} no longer points back"


def test_cursor_rule_frontmatter_is_always_applied() -> None:
    """A rule Cursor does not always apply is not a standing constraint."""
    body = _text(_CURSOR)
    assert body.startswith("---\n")
    front = body.split("---\n")[1]
    fields = dict(
        (k.strip(), v.strip()) for k, v in (ln.split(":", 1) for ln in front.strip().splitlines())
    )
    assert fields["alwaysApply"] == "true"
    assert "read-only" in fields["description"].lower()


def test_every_backticked_token_is_a_classified_claim() -> None:
    """Exhaustive both ways: no unchecked token, no stale classification."""
    in_files = _ticks(_COPILOT) | _ticks(_CURSOR)
    classified = {tok for group in _CLAIMS.values() for tok in group}
    assert in_files - classified == set(), "unclassified (so unchecked) tokens"
    assert classified - in_files == set(), "classified tokens no file uses any more"


# ------------------------------------------------- the claims, against the code


def test_named_symbols_resolve_in_the_package() -> None:
    for symbol in _SYMBOLS:
        if "." in symbol:
            module, attr = symbol.split(".", 1)
            path = _PKG / f"{module}.py"
            assert path.exists(), f"{symbol}: no module {module}.py"
            assert attr in _attributes(path) | _module_names(path), f"{symbol} not in {path.name}"
        else:
            assert any(
                symbol in _module_names(p) for p in sorted(_PKG.glob("*.py"))
            ), f"{symbol} is in no package module"


def test_named_tools_are_really_mcp_tools() -> None:
    tools = _tools()
    for name in ("get_book", "list_shelves"):
        assert name in tools, f"{name} is no longer an @mcp.tool"


def _returned_dict_keys(func: ast.AST) -> set[str]:
    """Keys of every dict literal this function returns."""
    keys: set[str] = set()
    for sub in ast.walk(func):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
            keys |= {
                k.value
                for k in sub.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return keys


def test_result_keys_and_tool_params_are_real() -> None:
    """Per tool, not per file: a whole-file grep is satisfied by any one tool.

    `returned` / `has_more` are the paging contract, so every tool that pages
    has to carry them; `url` is the citation contract, so every summary helper
    has to carry it. Renaming the key in one tool is then a failure rather
    than something the other seven cover for.
    """
    tools = _tools()
    routed = [n for n, f in tools.items() if "_paginated_graphql_edges" in _calls_in(f)]
    assert routed, "no tool pages through the shared helper any more"
    for name in sorted(routed):
        keys = _returned_dict_keys(tools[name])
        for key in ("returned", "has_more"):
            assert key in keys, f"{name} no longer returns {key!r}"

    summaries = [
        n
        for n in _module(_SERVER_PY).body
        if isinstance(n, ast.FunctionDef) and n.name.endswith("_summary")
    ]
    assert summaries, "server.py has no *_summary helpers any more"
    for helper in summaries:
        assert "url" in _returned_dict_keys(helper), f"{helper.name} no longer returns a url"

    params = {a.arg for f in tools.values() for a in f.args.args + f.args.kwonlyargs}
    for name in _TOOL_PARAMS:
        assert name in params, f"no @mcp.tool takes a {name!r} argument"


def test_tracked_files_exist() -> None:
    for rel in _TRACKED_FILES:
        assert (_ROOT / rel).is_file()
    for name in _PACKAGE_FILES:
        assert (_PKG / name).is_file()


def test_source_literals_appear_in_the_package() -> None:
    source = "\n".join(p.read_text(encoding="utf-8") for p in sorted(_PKG.glob("*.py")))
    for literal in _SOURCE_LITERALS:
        if literal == "/user/show/{uid}":
            continue  # checked against list_shelves' own f-string below
        assert literal in source, f"{literal!r} is claimed but appears nowhere in goodreads_mcp/"


# ------------------------------------------------ claim by claim, the hard ones


@pytest.mark.parametrize("rel", _DERIVED)
def test_the_xml_claim_matches_get_book(rel: str) -> None:
    """Both files say book pages are fetched via the `.xml` path, via get_book."""
    text = _squashed(rel)
    stated = re.findall(r"`(\.[a-z]+)`-suffixed", text)
    assert stated == [".xml"], f"{rel} names {stated} as the suffix"

    # The suffix is tested for and appended in two places; both are read out
    # of the AST and required to agree, so changing one is a failure rather
    # than something the other still satisfies a grep for.
    helpers = {
        n.name: n
        for n in _module(_SERVER_PY).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reachable = [helpers["get_book"]] + [
        helpers[c] for c in sorted(_calls_in(helpers["get_book"])) if c in helpers
    ]
    tested = {
        _skeleton(call.args[0])
        for func in reachable
        for call in ast.walk(func)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "endswith"
        and call.args
    }
    appended = {
        _skeleton(node.value)
        for func in reachable
        for node in ast.walk(func)
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add)
    }
    assert tested == appended == {".xml"}, (
        f"get_book tests for {tested} but appends {appended}; the docs say .xml"
    )


def test_the_list_shelves_scrape_claim_matches_list_shelves() -> None:
    """The one deliberate scrape: same tool, same params, same profile path."""
    body = _tools()["list_shelves"]
    literals = _strings_in(body)
    pattern = next(
        (s for s in literals if "shelf" in s and "tag" in s),
        None,
    )
    assert pattern, "list_shelves no longer regexes both shelf= and tag="
    assert "/user/show/{uid}" in literals, "list_shelves no longer fetches /user/show/{uid}"

    # The copilot file states the path and the params; both files state that
    # list_shelves is the only tool allowed to scrape.
    copilot = _squashed(_COPILOT)
    assert "`/user/show/{uid}`" in copilot
    assert "`shelf=`" in copilot and "`tag=`" in copilot
    # Exactly one tool scrapes, so every sentence that says who does must say
    # so in the same breath. A correct "list_shelves is the exception" three
    # bullets away does not excuse a sentence handing the scrape to anything
    # else. The prohibition on extending it ("scraping", the verb) is a
    # separate sentence and is required separately.
    scraper = {name for name, func in _tools().items() if "/user/show/{uid}" in _strings_in(func)}
    assert scraper == {"list_shelves"}, f"more than one tool scrapes HTML now: {scraper}"
    for rel in _DERIVED:
        squashed = _squashed(rel)
        attributions = [s for s in re.split(r"(?<=[.!?]) ", squashed) if re.search(r"\bscrape\b", s)]
        assert attributions, f"{rel} no longer says where the scraped surface is used"
        for sentence in attributions:
            assert "`list_shelves`" in sentence, (
                f"{rel} attributes the scrape without naming list_shelves: {sentence!r}"
            )
        assert re.search(r"(don't|do not) extend", squashed, re.I), (
            f"{rel} no longer forbids extending the scrape"
        )


@pytest.mark.parametrize("rel", _DERIVED)
def test_the_waf_status_code_is_the_one_the_client_tests_for(rel: str) -> None:
    stated = {int(m) for m in re.findall(r"HTTP (\d{3})", _squashed(rel))}
    detected = _status_codes_in("_is_waf_challenge", _CLIENT_PY)
    assert stated == detected, f"{rel} says {stated}, _is_waf_challenge tests {detected}"


@pytest.mark.parametrize("rel", _DERIVED)
def test_the_backoff_codes_are_the_ones_the_client_retries(rel: str) -> None:
    stated = set()
    for a, b in re.findall(r"\b(\d{3})/(\d{3})\b", _squashed(rel)):
        stated |= {int(a), int(b)}
    retried = _status_codes_in("_request", _CLIENT_PY)
    assert stated == retried, f"{rel} says {stated}, _request retries {retried}"


@pytest.mark.parametrize("rel", _DERIVED)
def test_graphql_config_is_resolved_at_runtime_and_nothing_hardcodes_it(rel: str) -> None:
    """The claim is "never hardcode"; the check is that nothing does.

    The negation carries the whole rule, so it is what gets matched: a file
    left saying "hardcode the GraphQL key" still contains the word.
    """
    assert re.search(r"\b(never|don't|do not)\s+hardcode\b", _squashed(rel), re.I), (
        f"{rel} no longer forbids hardcoding the GraphQL key and endpoint"
    )
    for path in sorted(_PKG.glob("*.py")):
        for node in ast.walk(_module(path)):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            value = node.value.strip()
            # A regex that *matches* the endpoint is how it is discovered; a
            # string literal that *is* the endpoint is the thing forbidden.
            if value.startswith("https://") and "appsync-api" in value:
                pytest.fail(f"{path.name} hardcodes an AppSync endpoint: {value!r}")
    graphql = next(
        n
        for n in ast.walk(_module(_CLIENT_PY))
        if isinstance(n, ast.FunctionDef) and n.name == "graphql"
    )
    assert "graphql_config" in _calls_in(graphql)


def test_the_versions_the_copilot_file_says_to_keep_in_sync_are_in_sync() -> None:
    project = re.search(r'^version\s*=\s*"([^"]+)"', _text("pyproject.toml"), re.M)
    manifest = json.loads(_text("manifest.json"))
    assert project, "pyproject.toml has no version"
    assert project.group(1) == manifest["version"]
    assert "in sync" in _squashed(_COPILOT)


# --------------------------------------------- the carve-out that went missing


def test_the_exception_tools_are_derived_not_listed() -> None:
    """Guards the derivation the next test depends on."""
    consts = _int_constants()
    caps = _caps_by_tool()
    assert caps.get("similar_books") == {"_MAX_DISCOVERY"}
    tighter = {
        tool
        for tool, used in caps.items()
        if all(consts[c] != consts["_MAX_DISCOVERY"] for c in used)
    }
    assert tighter == {"popular_books", "compare_books"}, f"cap exceptions changed: {tighter}"


def test_a_stated_cap_names_every_tool_that_does_not_obey_it() -> None:
    """State the number as a rule and you inherit the duty to state the exceptions.

    AGENTS.md is checked alongside the copies: it is the source, so if it ever
    loses the carve-out the copies have nothing to be copied from. The set of
    files that state the number is asserted rather than discovered quietly —
    a copy that starts quoting the cap joins this check instead of slipping
    past it.
    """
    consts = _int_constants()
    cap = consts["_MAX_DISCOVERY"]
    candidates = ("AGENTS.md",) + _DERIVED
    stating = {rel for rel in candidates if re.search(rf"cap\D{{0,20}}\b{cap}\b", _squashed(rel))}
    assert stating == {"AGENTS.md", _CURSOR}, f"who states the {cap} cap changed: {stating}"

    caps = _caps_by_tool()
    tighter = {tool for tool, used in caps.items() if all(consts[c] != cap for c in used)}
    detail = ", ".join(f"{t}={min(consts[c] for c in caps[t])}" for t in sorted(tighter))
    for rel in sorted(stating):
        text = _squashed(rel)
        missing = {tool for tool in tighter if f"`{tool}`" not in text}
        assert not missing, (
            f"{rel} states the {cap} cap as a rule but never names {sorted(missing)}, "
            f"whose caps are tighter: {detail}"
        )


def test_every_tool_routed_through_the_shared_paginator_uses_the_shared_cap() -> None:
    """The other half of the same convention, checked on the code side."""
    routed = {
        name for name, func in _tools().items() if "_paginated_graphql_edges" in _calls_in(func)
    }
    assert routed, "no tool reuses _paginated_graphql_edges any more"
    for name in sorted(routed):
        assert "_validate_discovery_limit" in _calls_in(_tools()[name]), (
            f"{name} pages through the shared helper but clamps its limit some other way"
        )
    assert "`_paginated_graphql_edges`" in _squashed(_CURSOR)


def test_every_tool_is_annotated_read_only() -> None:
    """Both files open with "read-only"; the annotation is what makes it true."""
    for name, func in _tools().items():
        annotations = {
            kw.value.id
            for dec in func.decorator_list
            if isinstance(dec, ast.Call)
            for kw in dec.keywords
            if kw.arg == "annotations" and isinstance(kw.value, ast.Name)
        }
        assert annotations == {"_READ_ONLY"}, f"{name} is not annotated read-only"
    # The Cursor file says it twice — once in its frontmatter description and
    # once in its first bullet — so the body is checked separately from the
    # description, and the opposite claim is forbidden outright.
    for rel in _DERIVED:
        body = _squashed(rel).split("---", 2)[-1].lower()
        assert "read-only" in body, f"{rel}'s body no longer says the server is read-only"
        assert "read-write" not in body, f"{rel} calls the server read-write"
