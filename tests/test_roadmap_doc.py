"""docs/roadmap.md lists what is NOT built; these fail the day one of its items is.

The page has rotted once already: it cited #153 (PyPI publishing) as open
roadmap work for a day after publishing shipped, and a maintainer had to
notice by hand (6aa4bc5). Nothing read the page, so nothing could notice.

Each item makes a checkable claim about the code: no tool returns author bio,
photo or follower count, `author_books` points at the author page through
`author_url` instead, and the only cache is the per-process GraphQL key and
endpoint behind `client.graphql_config`, and the server is pinned below
`mcp` 2.x because it still imports `mcp.server.fastmcp`. The checks below fail when the code
outgrows a claim, so whoever ships the feature also updates the page.
docs/strategy.md names the same items in one sentence, so that sentence is
joined to the page's bullets as well.
"""

import ast
import re
import tomllib
from pathlib import Path

from goodreads_mcp import client, server

ROOT = Path(__file__).resolve().parent.parent
ROADMAP = (ROOT / "docs" / "roadmap.md").read_text()
STRATEGY = (ROOT / "docs" / "strategy.md").read_text()
PACKAGE = ROOT / "goodreads_mcp"

# The author-page fields the "Author page detail" item says are not exposed.
_AUTHOR_DETAIL_KEYS = {"bio", "author_bio", "photo", "author_photo", "followers", "follower_count"}
# Distributions that would add a response or memo cache.
_CACHE_DISTRIBUTIONS = ("cachetools", "hishel", "requests-cache", "diskcache", "aiocache", "cachecontrol")


def _items():
    items = re.findall(r"^- \*\*(.+?)\*\*", ROADMAP, re.M)
    assert items, "docs/roadmap.md no longer lists its ideas as '- **Name**' bullets"
    return [item.rstrip(".").lower() for item in items]


def _strategy_names():
    sentence = re.search(r"\[`roadmap\.md`\]\(roadmap\.md\) lists ideas that are not built: ((?:[^.]|\.(?=\S))+)\.", STRATEGY)
    assert sentence, "docs/strategy.md no longer names the roadmap's ideas in its 'What is not decided' sentence"
    names = re.split(r",\s*(?:and\s+)?|\s+and\s+", " ".join(sentence.group(1).split()))
    return [re.sub(r"^(?:a|an|the)\s+", "", name.strip()).lower() for name in names if name.strip()]


def test_strategy_names_exactly_the_roadmap_items():
    items, names = _items(), _strategy_names()
    assert len(names) == len(items), f"docs/strategy.md names {names}; docs/roadmap.md lists {items}"
    for name in names:
        matches = [item for item in items if item.startswith(name)]
        assert len(matches) == 1, f"docs/strategy.md names {name!r}, which matches roadmap items {matches}"


def test_the_items_checked_below_are_still_on_the_page():
    # If an item is dropped because it shipped, delete its check below with it.
    assert [item.split()[0] for item in _items()] == ["author", "caching", "`mcp`"]


def test_no_tool_exposes_author_page_detail():
    tools = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    author_tools = sorted(name for name in tools if "author" in name)
    assert author_tools == ["author_books"], f"new author tools {author_tools}: update docs/roadmap.md"
    keys = {
        key.value
        for node in ast.walk(ast.parse((PACKAGE / "server.py").read_text()))
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    shipped = sorted(keys & _AUTHOR_DETAIL_KEYS)
    assert not shipped, f"server.py now returns {shipped}: docs/roadmap.md says author detail is not built"


def test_author_books_returns_the_author_url_the_roadmap_points_to(monkeypatch):
    assert "`author_books` links to the page (`author_url`)" in ROADMAP
    monkeypatch.setattr(
        server,
        "_resolve_book_ids",
        lambda book_id: {
            "contributor_kca": "kca://author/1",
            "contributor_name": "Ann Author",
            "contributor_url": "https://www.goodreads.com/author/show/1",
        },
    )
    monkeypatch.setattr(server, "_paginated_graphql_edges", lambda *args: ([], False, 0))
    result = server.author_books("1")
    assert result["author_url"] == "https://www.goodreads.com/author/show/1"


def test_graphql_config_is_the_only_cache():
    assert "Today the only cache is the per-process GraphQL key and endpoint (`client.graphql_config`)." in ROADMAP
    assert callable(getattr(client.GoodreadsClient, "graphql_config", None))
    allowed = {"graphql_config", "_cached_config"}
    found = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text())
        exempt = {
            id(inner)
            for cls in ast.walk(tree)
            if isinstance(cls, ast.ClassDef) and cls.name == "GoodreadsClient"
            for fn in cls.body
            if isinstance(fn, ast.FunctionDef) and fn.name in allowed
            for inner in ast.walk(fn)
        }
        for node in ast.walk(tree):
            if id(node) in exempt:
                continue
            names = []
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.arg)):
                names.append(getattr(node, "name", None) or getattr(node, "arg", ""))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names += [alias.name for alias in node.names] + [getattr(node, "module", None) or ""]
            found += [f"{path.name}:{node.lineno} {name}" for name in names if re.search("cache|memo", name, re.I)]
    assert not found, f"a cache outside client.graphql_config: {found}; update docs/roadmap.md"


def test_no_dependency_brings_a_cache():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    declared = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        declared += extra
    names = {re.match(r"[A-Za-z0-9_.-]+", spec).group(0).lower().replace("_", "-") for spec in declared}
    assert not names & set(_CACHE_DISTRIBUTIONS), "a cache library is now a dependency: update docs/roadmap.md"



def test_mcp_stays_below_2_while_the_server_imports_fastmcp():
    assert "`mcp[cli]>=1.21.1,<2` in `pyproject.toml`" in ROADMAP
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert "mcp[cli]>=1.21.1,<2" in dependencies, f"the mcp pin moved ({dependencies}): update docs/roadmap.md"
    imports = {
        node.module
        for node in ast.walk(ast.parse((PACKAGE / "server.py").read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "mcp.server.fastmcp" in imports, "server.py no longer imports mcp.server.fastmcp: update docs/roadmap.md"
