"""The two add-a-tool recipes, read as subjects rather than sources.

`prompts/add-discovery-tool.md` (a prompt to paste into an agent) and
`.claude/skills/add-mcp-tool/SKILL.md` (the same recipe as a Claude Code
skill) tell an agent how to add a tool: which decorator, which pagination
helper, which caps, which result keys, which helpers shape results, where the
tests go and which copies of the tool registry to update. Before this file the
only test that opened either was `test_coverage_thresholds.py`, which takes the
`55` out of each and nothing else.

The registry step had drifted by omission. Both recipes, and rubric §6 that a
reviewer holds the PR to, named two copies: the `README.md` table and the
`server.py` docstring. Following the prompt exactly on a scratch branch turned
three offline tests red, because three more committed files pin what adding a
tool changes: `AGENTS.md` counts "the 12 `@mcp.tool` functions", the
2026-09 reflection counts `_paginated_graphql_edges`' call sites (and the
recipe tells you to call it), and `docs/quality.md` counts the collected tests.
An agent doing exactly what the recipe said produced a PR that could not go
green. The recipes now name all of them, and
`test_every_registry_copy_found_in_the_tree_is_in_the_declared_set` finds the
copies from the tree rather than from a list, so a fifth copy has to be named
in every recipe before it can land.

Every other claim is read out of the source, not restated, and the backtick
vocabulary of both recipes is partitioned exhaustively (`_kind`) so a new
token must be classified before it can land unchecked.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

import pytest

from goodreads_mcp import server

_ROOT = Path(__file__).resolve().parent.parent
_PROMPT = "prompts/add-discovery-tool.md"
_SKILL = ".claude/skills/add-mcp-tool/SKILL.md"
_RUBRIC = "docs/review-rubric.md"
_RECIPES = (_PROMPT, _SKILL)

_PKG = _ROOT / "goodreads_mcp"
_SERVER_PY = _PKG / "server.py"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_HELPER = "_paginated_graphql_edges"

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
}

# Every committed copy of what adding a tool changes, mapped to how it is
# spelled in a recipe. `test_every_registry_copy_found_in_the_tree_is_in_the_declared_set`
# derives this set from the tree and compares both ways, so it cannot go stale.
_REGISTRY_COPIES = {
    "README.md": "README.md",
    "goodreads_mcp/server.py": "server.py",
    "AGENTS.md": "AGENTS.md",
    "docs/reflections/2026-09-verification.md": "docs/reflections/2026-09-verification.md",
}
# Pinned by `test_coverage_thresholds.py`; every new test moves it.
_COUNT_ROW_FILE = "docs/quality.md"


def _text(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _squash(text: str) -> str:
    """Markdown wraps; a phrase can break across lines."""
    return re.sub(r"\s+", " ", text)


def _prose(rel: str) -> str:
    """The file without fenced code blocks or YAML frontmatter."""
    text = re.sub(r"\A---\n.*?\n---\n", "", _text(rel), flags=re.S)
    return re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)


def _backticks(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", text)


def _section(rel: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", _text(rel), re.M | re.S)
    assert match, f"{rel} has no `## {heading}` section"
    return match.group(1)


def _paragraph(rel: str, opener: str) -> str:
    """The blank-line-delimited paragraph that starts with `opener`."""
    for para in re.split(r"\n\s*\n", _prose(rel)):
        if para.lstrip().startswith(opener):
            return para
    raise AssertionError(f"{rel} has no paragraph starting {opener!r}")


def _registry_step(rel: str) -> str:
    if rel == _PROMPT:
        return _paragraph(rel, "Finally")
    if rel == _SKILL:
        return _section(rel, "Document")
    item = re.search(r"^- \[ \] New tool added.*?(?=^- \[ \]|\Z)", _section(rel, "6. Housekeeping"), re.M | re.S)
    assert item, f"{rel} §6 no longer has the new-tool checklist item"
    return item.group(0)


def _test_step(rel: str) -> str:
    return _paragraph(rel, "Tests:") if rel == _PROMPT else _section(rel, "Test")


# ------------------------------------------------------------------ the AST

_TREE = ast.parse(_SERVER_PY.read_text(encoding="utf-8"))
_SOURCE = _SERVER_PY.read_text(encoding="utf-8")


def _is_tool_decorator(dec: ast.expr) -> bool:
    target = dec.func if isinstance(dec, ast.Call) else dec
    return ast.unparse(target) == "mcp.tool"


_TOOLS = {
    node.name: node
    for node in _TREE.body
    if isinstance(node, ast.FunctionDef) and any(_is_tool_decorator(d) for d in node.decorator_list)
}

_TOP_LEVEL: dict[str, ast.AST] = {}
for _node in _TREE.body:
    if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        _TOP_LEVEL[_node.name] = _node
    elif isinstance(_node, ast.Assign):
        for _target in _node.targets:
            if isinstance(_target, ast.Name):
                _TOP_LEVEL[_target.id] = _node
    elif isinstance(_node, ast.AnnAssign) and isinstance(_node.target, ast.Name):
        _TOP_LEVEL[_node.target.id] = _node


def _constant(name: str):
    node = _TOP_LEVEL.get(name)
    assert isinstance(node, ast.Assign), f"server.py has no module-level `{name} = ...`"
    return ast.literal_eval(node.value)


def _calls(func: ast.AST, name: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def _names_in(func: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(func) if isinstance(n, ast.Name)}


def _return_keys(func: ast.AST) -> set[str]:
    keys: set[str] = set()
    for ret in (n for n in ast.walk(func) if isinstance(n, ast.Return)):
        for d in (n for n in ast.walk(ret) if isinstance(n, ast.Dict)):
            keys |= {k.value for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


_QUERY_CONSTANTS = {
    name: _constant(name) for name in _TOP_LEVEL if name.startswith("_Q_")
}
_HELPER_TOOLS = {name: fn for name, fn in _TOOLS.items() if _calls(fn, _HELPER)}


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return out.split()


_TRACKED = _tracked()


def test_there_is_something_to_check():
    assert len(_TOOLS) >= 10, f"found only {sorted(_TOOLS)} decorated tools"
    assert _HELPER_TOOLS, f"no tool calls {_HELPER}"
    assert _QUERY_CONSTANTS, "no `_Q_*` query documents in server.py"


# ============================================ the registry step (the drift)


def _number(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBER_WORDS[word.lower()]


_COUNT_CLAIMS = (
    # "the 12 `@mcp.tool` functions"
    re.compile(r"\b(?P<n>\d+|[a-z]+) `@mcp\.tool` functions"),
    # "`_paginated_graphql_edges` ... has five call sites"
    re.compile(r"\bhas (?P<n>\d+|[a-z]+) call sites\b"),
)


def _found_copies() -> set[str]:
    """Every tracked file outside `tests/` that pins the tool registry: it names
    every decorated tool, or it states a count that adding a tool changes."""
    found = set()
    for rel in _TRACKED:
        if rel.startswith("tests/") or not rel.endswith((".md", ".mdc", ".py", ".json", ".txt")):
            continue
        text = _text(rel)
        if all(re.search(rf"\b{name}\b", text) for name in _TOOLS):
            found.add(rel)
        squashed = _squash(text)
        for pattern in _COUNT_CLAIMS:
            for m in pattern.finditer(squashed):
                if m.group("n").isdigit() or m.group("n").lower() in _NUMBER_WORDS:
                    found.add(rel)
    return found


def test_every_registry_copy_found_in_the_tree_is_in_the_declared_set():
    found = _found_copies()
    assert found == set(_REGISTRY_COPIES), (
        "the files that pin the tool registry changed. Found in the tree but not "
        f"declared: {sorted(found - set(_REGISTRY_COPIES))}; declared but no longer "
        f"a copy: {sorted(set(_REGISTRY_COPIES) - found)}. Update _REGISTRY_COPIES "
        "and every recipe's registry step together."
    )


def test_each_count_a_copy_states_is_the_real_one():
    """The counts are held true elsewhere too; here they prove the declared
    copies really do change when a tool is added the recipe's way."""
    agents = re.search(_COUNT_CLAIMS[0], _squash(_text("AGENTS.md")))
    assert agents and _number(agents.group("n")) == len(_TOOLS)
    reflection = re.search(_COUNT_CLAIMS[1], _squash(_text("docs/reflections/2026-09-verification.md")))
    helper = _TOP_LEVEL[_HELPER]
    call_sites = [
        c for c in _calls(_TREE, _HELPER)
        if not helper.lineno <= c.lineno <= (helper.end_lineno or helper.lineno)
    ]
    assert reflection and _number(reflection.group("n")) == len(call_sites)


@pytest.mark.parametrize("recipe", (*_RECIPES, _RUBRIC))
def test_the_registry_step_names_every_copy(recipe):
    named = set(_backticks(_registry_step(recipe)))
    missing = {rel for rel, spelling in _REGISTRY_COPIES.items() if spelling not in named}
    assert not missing, (
        f"{recipe}'s registry step omits {sorted(missing)}; a tool added by "
        "following it leaves those copies stale and their tests red"
    )


@pytest.mark.parametrize("recipe", _RECIPES)
def test_the_test_step_names_the_count_row_new_tests_move(recipe):
    step = _test_step(recipe)
    assert f"`{_COUNT_ROW_FILE}`" in step, f"{recipe}'s test step does not name {_COUNT_ROW_FILE}"
    assert "| offline tests |" in _text(_COUNT_ROW_FILE), f"{_COUNT_ROW_FILE} lost its count row"


@pytest.mark.parametrize("recipe", _RECIPES)
def test_the_registry_step_says_what_each_copy_needs(recipe):
    """Naming the file is not enough when two of them are counts, not lists."""
    step = _squash(_registry_step(recipe))
    assert re.search(r"table in `README\.md`", step)
    assert re.search(r"docstring list at the top of `server\.py`", step)
    assert re.search(r"`@mcp\.tool` count in `AGENTS\.md`", step)
    assert re.search(rf"`{_HELPER}` call-site count in `docs/reflections/", step)


# ===================================================== the code conventions


@pytest.mark.parametrize("recipe", _RECIPES)
def test_every_cap_the_recipe_quotes_is_the_constants_value(recipe):
    quoted = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"`(_[A-Z_]+)` \((\d+)\)", _squash(_prose(recipe)))
    }
    assert {"_MAX_DISCOVERY", "_DISCOVERY_PAGE_SIZE"} <= set(quoted), (
        f"{recipe} no longer quotes both discovery caps: {quoted}"
    )
    for name, value in quoted.items():
        assert _constant(name) == value, f"{recipe} says `{name}` ({value}); server.py has {_constant(name)}"


def test_the_caps_mean_what_the_recipes_say():
    """`_MAX_DISCOVERY` is the total, `_DISCOVERY_PAGE_SIZE` the per-request size."""
    validate = _TOP_LEVEL["_validate_discovery_limit"]
    assert "_MAX_DISCOVERY" in _names_in(validate)
    helper = _TOP_LEVEL[_HELPER]
    assert _calls(helper, "_validate_discovery_limit"), f"{_HELPER} no longer caps the total"
    assert "_DISCOVERY_PAGE_SIZE" in _names_in(helper), f"{_HELPER} no longer sizes pages"


def test_the_decorator_snippet_is_how_every_tool_is_decorated():
    snippets = {
        rel: re.findall(r"@mcp\.tool\([^)\n]*\)", _text(rel)) for rel in _RECIPES
    }
    assert all(snippets.values()), f"a recipe lost its decorator snippet: {snippets}"
    for rel, found in snippets.items():
        for tool, fn in _TOOLS.items():
            spelled = [
                "@" + ast.get_source_segment(_SOURCE, d)
                for d in fn.decorator_list if _is_tool_decorator(d)
            ]
            assert spelled == [found[0]], f"{rel} says {found[0]}; {tool} is decorated {spelled}"


def test_every_query_the_helper_pages_uses_the_standard_shapes():
    """ "every supported connection uses ... `PaginationInput` / `PageInfo`" """
    for tool, fn in _HELPER_TOOLS.items():
        for call in _calls(fn, _HELPER):
            first = call.args[0]
            assert isinstance(first, ast.Name) and first.id in _QUERY_CONSTANTS, (
                f"{tool} pages a query that is not a `_Q_*` constant"
            )
            doc = re.sub(r"\s+", "", _QUERY_CONSTANTS[first.id])
            assert "$pagination:PaginationInput" in doc, f"{first.id} does not take PaginationInput"
            assert "pageInfo{hasNextPagenextPageToken}" in doc, (
                f"{first.id} does not select the PageInfo fields {_HELPER} reads"
            )
    helper = _string_constants(_SERVER_PY)
    assert {"pageInfo", "hasNextPage", "nextPageToken"} <= set(helper)


def test_every_tool_on_the_helper_returns_the_keys_the_recipes_require():
    for rel in _RECIPES:
        sentence = re.search(r"Return (`\w+`) and (`\w+`)", _squash(_prose(rel)))
        assert sentence, f"{rel} no longer says which keys to return"
        keys = {sentence.group(1).strip("`"), sentence.group(2).strip("`")}
        assert keys == {"returned", "has_more"}, f"{rel} requires {keys}"
        for tool, fn in _HELPER_TOOLS.items():
            assert keys <= _return_keys(fn), f"{tool} does not return {keys - _return_keys(fn)}"


@pytest.mark.parametrize("shaper", ["_book_summary", "_work_summary", "_node_summary"])
def test_every_result_shaper_carries_the_citation_url(shaper):
    assert {"book_id", "url"} <= _return_keys(_TOP_LEVEL[shaper]), (
        f"{shaper} dropped `book_id` or `url`; the recipes promise both"
    )


def test_server_instructions_tell_the_model_to_cite_from_url():
    instructions = _squash(server.SERVER_INSTRUCTIONS)
    assert re.search(r"\bcit", instructions, re.I) and "'url'" in instructions


def test_no_graphql_key_or_endpoint_is_hardcoded():
    """ "Never hardcode the GraphQL key or endpoint." Discovery regexes are fine;
    a concrete key or host in a string literal is not."""
    # The negation carries the rule; "hardcode" alone would survive losing it.
    assert re.search(r"(never|don't|do not)\s+hardcode the GraphQL key or endpoint", _squash(_text(_PROMPT)), re.I)
    key = re.compile(r"da2-[a-z0-9]{10,}")
    host = re.compile(r"https://[a-z0-9]+\.appsync-api\.[a-z0-9-]+\.amazonaws\.com")
    for path in sorted(_PKG.glob("*.py")):
        for literal in _string_constants(path):
            assert not key.search(literal), f"{path.name} hardcodes an AppSync key"
            assert not host.search(literal), f"{path.name} hardcodes an AppSync endpoint"


def test_the_fan_out_example_enforces_its_cap():
    assert "_MAX_COMPARE" in _names_in(_TOOLS["compare_books"])


def test_the_slug_example_resolves_to_its_numeric_id():
    slug = next(t for t in _backticks(_prose(_SKILL)) if re.fullmatch(r"\d+-[a-z0-9-]+", t))
    assert server._legacy_id(slug) == int(slug.split("-", 1)[0])
    assert {"_resolve_book_ids", "_legacy_id"} <= set(_TOP_LEVEL)


def test_the_skill_scrape_exception_is_the_only_tool_on_scraped_html():
    """ "`list_shelves` does it only because shelf *names* have no structured
    surface." AGENTS.md's data-surfaces list says the same; hold the skill to it."""
    text = _squash(_prose(_SKILL))
    assert re.search(r"`list_shelves` does it only", text)
    assert re.search(r"Scraped HTML\*\* — `list_shelves` only", _squash(_text("AGENTS.md")))


# ============================================ the vocabulary, exhaustively


def _is_path(token: str) -> bool:
    if token in _TRACKED:
        return True
    if token.endswith("/"):
        return any(rel.startswith(token) for rel in _TRACKED)
    return sum(1 for rel in _TRACKED if rel.rsplit("/", 1)[-1] == token and rel.startswith("goodreads_mcp/")) == 1


def _all_return_keys() -> set[str]:
    keys: set[str] = set()
    for node in _TOP_LEVEL.values():
        keys |= _return_keys(node)
    return keys


def _ci_runs(token: str) -> bool:
    ci = _CI.read_text(encoding="utf-8")
    runs = re.findall(r"run: (pytest[^\n]*)", ci)
    return token.startswith("pytest ") and any(set(token.split()) <= set(r.split()) for r in runs)


def _is_graphql_name(token: str) -> bool:
    docs = " ".join(_QUERY_CONSTANTS.values())
    return re.fullmatch(r"[A-Z]\w+", token) is not None and (
        re.search(rf"\b{token}\b", docs) is not None
        or re.search(rf"\b{token[0].lower()}{token[1:]}\s*\{{", docs) is not None
    )


def _kind(token: str) -> str | None:
    if token.startswith("@mcp.tool"):
        return "decorator"
    if token in _TOOLS:
        return "tool"
    if token in _TOP_LEVEL:
        return "server symbol"
    if _is_path(token):
        return "tracked path"
    if token in _all_return_keys():
        return "result key"
    if _is_graphql_name(token):
        return "graphql type"
    if re.fullmatch(r"\d+-[a-z0-9-]+", token):
        return "slug example"
    if _ci_runs(token):
        return "ci command"
    if any(token in literal for path in _PKG.glob("*.py") for literal in _string_constants(path)):
        return "source literal"
    return None


@pytest.mark.parametrize("recipe", _RECIPES)
def test_every_backticked_token_is_a_claim_of_a_known_kind(recipe):
    unclassified = sorted({t for t in _backticks(_prose(recipe)) if _kind(t) is None})
    assert not unclassified, (
        f"{recipe} names {unclassified}, which is none of: a decorated tool, a "
        "server.py symbol, a tracked path, a result key, a GraphQL type, the slug "
        "example, a CI command or a source literal. Classify it here so it is checked."
    )
