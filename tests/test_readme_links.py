"""Every link in README.md points at something that exists (#189).

README.md is the PyPI project description, so its links are absolute
(#185, `tests/test_packaging_metadata.py`) and a dead one ships in every
release. This module resolves the targets: a `blob/main/<path>` link names a
tracked file, a `#anchor` names a heading in that file, a `tree/main/<dir>/`
link names a tracked directory, a badge names a workflow file, and the PyPI
link names this distribution. Any link into this repository that fits none of
those shapes fails, so a new kind of link has to be classified here before it
is trusted. A link whose text is a path is held to its target, so text and
target cannot drift apart.

The `## documentation` list is also checked against `docs/*.md`: a page is
either listed or named in `_NOT_IN_INDEX`, so leaving one out is a visible
choice rather than an oversight.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
_README = _PROJECT["readme"]
_REPO_URL = _PROJECT["urls"]["Repository"]
_REPO_PATH = urlsplit(_REPO_URL).path  # `/Danathar/goodreads-mcp`

# The branch every README link pins. Release CI only tags commits on `main`
# (CONTRIBUTING.md, Releases), so a link to any other branch rots as soon as
# that branch is deleted.
_BRANCH = "main"

# Top-level `docs/*.md` pages the `## documentation` list leaves out on
# purpose, each with the reason. Adding a page to `docs/` without either
# listing it or naming it here fails `test_the_documentation_index_lists_every_docs_page`.
_NOT_IN_INDEX = {
    # Reached from docs/maintenance.md and docs/SECURITY-AI.md, which are listed.
    "docs/branch-protection.md",
}

# Markdown `[text](target)` / `![alt](target)` with no bracket inside the text.
# `_md_links` peels a badge, `[![alt](image)](page)`, from the inside out.
_MD_LINK = re.compile(r"!?\[([^\[\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# HTML allows whitespace around `=`; the same spacing is accepted everywhere
# an attribute is read or guarded below and in tests/test_packaging_metadata.py.
_HTML_LINK = re.compile(r'\b(?:src|href)\s*=\s*"([^"]+)"')
# Link forms `_MD_LINK` and `_HTML_LINK` do not see. Any of these in the README
# would carry a target no test resolves, so their presence fails
# `test_the_readme_uses_only_link_forms_this_file_resolves`.
_UNSUPPORTED_LINK_FORMS = {
    "reference-style link `[text][ref]` / `[text][]`": re.compile(r"\]\[[^\]]*\]"),
    "reference definition `[ref]: target`": re.compile(r"^[ \t]{0,3}\[[^\]]+\]:[ \t]", re.M),
    "autolink `<https://...>`": re.compile(r"<https?://[^>\s]+>"),
    # `_HTML_LINK` reads double-quoted attributes only; the other two spellings
    # HTML allows would otherwise be a target nothing checks.
    "single-quoted or unquoted HTML attribute `href='...'` / `src=...`": re.compile(r"\b(?:src|href)\s*=\s*(?:'|[^\"'\s])"),
    # Anything else: a URL not opened by `(`, `"`, `<` or a backtick. A URL
    # after `'` counts, so a single-quoted attribute is caught twice, on
    # purpose, and a URL quoted in prose is not a hidden link.
    "bare URL": re.compile(r"(?<![(\"<`])https?://[^\s)\"'>`]+"),
}
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$", re.M)
# Link text that reads as a path: `docs/design.md`, `ci.yml`, `docs/reflections/`,
# `LICENSE.MIT`, `LICENSE`. A word with neither a slash nor a dot is prose,
# except the bare licence file names.
_PATH_TEXT = re.compile(r"[\w.\-]*(?:/[\w.\-]*)*(?:\.[A-Za-z0-9]+|/)|LICENSE(?:\.[A-Z]+)?")


def _tracked() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return set(out.split())


_TRACKED = _tracked()
_TRACKED_DIRS = {str(parent) + "/" for path in _TRACKED for parent in Path(path).parents if parent != Path(".")}


def _prose(rel: str) -> str:
    """The file without fenced code blocks, where `# comment` is not a heading and
    `](x)` is not a link."""
    return re.sub(r"^```.*?^```", "", (_ROOT / rel).read_text(encoding="utf-8"), flags=re.S | re.M)


def _slugs(rel: str) -> set[str]:
    """The anchors GitHub renders for a Markdown file's headings.

    GitHub lowercases the heading, drops everything but letters, digits,
    spaces, hyphens and underscores, turns spaces into hyphens, and suffixes
    `-1`, `-2`, ... to repeats. Emoji are dropped, so `# 📚 goodreads-mcp`
    becomes `-goodreads-mcp`.
    """
    seen: dict[str, int] = {}
    slugs: set[str] = set()
    for _, raw in _HEADING.findall(_prose(rel)):
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw)  # a linked heading keeps its text
        text = text.replace("`", "")
        slug = re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text.lower()))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        slugs.add(slug if count == 0 else f"{slug}-{count}")
    return slugs


def _md_links(markdown: str) -> list[tuple[str, str]]:
    """Every (text, target), innermost first: `[![CI](image)](page)` yields the
    image and then, with the image reduced to its alt text, the page."""
    found: list[tuple[str, str]] = []
    while True:
        matches = list(_MD_LINK.finditer(markdown))
        if not matches:
            return found
        found += [(m.group(1), m.group(2)) for m in matches]
        markdown = _MD_LINK.sub(lambda m: m.group(1), markdown)


def _readme_links() -> list[tuple[str, str]]:
    """Every (text, target) in README.md; HTML `src`/`href` targets have no text."""
    prose = _prose(_README)
    links = _md_links(prose) + [("", target) for target in _HTML_LINK.findall(prose)]
    assert links, f"found no links in {_README}; the pattern is wrong"
    return links


_LINKS = _readme_links()
_IDS = [f"{text or '<html>'} -> {target}" for text, target in _LINKS]


def _classify(target: str) -> tuple[str, str]:
    """(kind, path) for a target, where `path` is what the kind must resolve.

    Kinds: `file` (`blob/<branch>/<path>[#anchor]`, path may carry `#anchor`),
    `dir` (`tree/<branch>/<dir>/`), `readme-anchor` (`<repo>#anchor`),
    `workflow` (`actions/workflows/<file>[/badge.svg?branch=<branch>]`),
    `releases`, `pypi`, and `external` for anything outside this repository.
    """
    parts = urlsplit(target)
    if parts.scheme == "https" and parts.netloc == "pypi.org":
        return "pypi", parts.path
    if parts.netloc != "github.com" or not (parts.path == _REPO_PATH or parts.path.startswith(_REPO_PATH + "/")):
        return "external", target
    rest = parts.path[len(_REPO_PATH):]
    if rest == "" and parts.fragment:
        return "readme-anchor", parts.fragment
    if rest == "/releases":
        return "releases", ""
    m = re.fullmatch(r"/blob/([^/]+)/(.+)", rest)
    if m:
        assert m.group(1) == _BRANCH, f"{target} pins branch {m.group(1)!r}, not {_BRANCH!r}"
        return "file", m.group(2) + ("#" + parts.fragment if parts.fragment else "")
    m = re.fullmatch(r"/tree/([^/]+)/(.+/)", rest)
    if m:
        assert m.group(1) == _BRANCH, f"{target} pins branch {m.group(1)!r}, not {_BRANCH!r}"
        assert not parts.fragment, f"{target}: a directory has no headings to anchor"
        return "dir", m.group(2)
    m = re.fullmatch(r"/actions/workflows/([^/]+)(?:/badge\.svg)?", rest)
    if m:
        if rest.endswith("/badge.svg"):
            assert parts.query == f"branch={_BRANCH}", f"{target}: the badge must read branch {_BRANCH!r}"
        else:
            assert not parts.query, f"{target}: unexpected query on a workflow page link"
        return "workflow", m.group(1)
    pytest.fail(
        f"{target} is a link into this repository of a shape this test does not resolve. "
        "Classify it in tests/test_readme_links.py::_classify so it is checked."
    )


def test_the_readme_uses_only_link_forms_this_file_resolves():
    """A reference-style link, its `[ref]: target` definition, an autolink or a
    bare URL is a target `_readme_links` never sees, so a dead one would ship
    unchecked. The README uses none today; this keeps it that way, or makes
    adding a parser for the form a visible step."""
    prose = _prose(_README)
    found = {name: pattern.findall(prose) for name, pattern in _UNSUPPORTED_LINK_FORMS.items()}
    found = {name: hits for name, hits in found.items() if hits}
    assert not found, (
        f"{_README} uses link forms tests/test_readme_links.py does not resolve: {found}. "
        "Rewrite them as inline `[text](target)` links, or extend `_readme_links` to parse the form."
    )


def test_the_link_pattern_finds_the_absolute_link_test_finds():
    """Same targets as `test_the_readme_links_work_on_the_pypi_page`, so neither
    pattern silently sees a subset of the README."""
    raw = (_ROOT / _README).read_text(encoding="utf-8")
    loose = re.findall(r"\]\(([^)\s]+)\)", raw) + re.findall(r'\b(?:src|href)\s*=\s*"([^"]+)"', raw)
    assert sorted(loose) == sorted(target for _, target in _LINKS)


def test_every_link_target_kind_is_represented():
    """The classifier's branches are exercised: the README carries at least one
    link of each shape it knows, so a branch cannot rot untested."""
    kinds = {_classify(target)[0] for _, target in _LINKS}
    assert kinds == {"file", "dir", "readme-anchor", "workflow", "releases", "pypi", "external"}


@pytest.mark.parametrize(("text", "target"), _LINKS, ids=_IDS)
def test_the_link_resolves(text: str, target: str):
    kind, path = _classify(target)
    if kind == "file":
        rel, _, anchor = path.partition("#")
        assert rel in _TRACKED, f"{target}: {rel!r} is not a tracked file"
        if anchor:
            assert rel.endswith(".md"), f"{target}: an anchor into a non-Markdown file goes nowhere"
            slugs = _slugs(rel)
            assert anchor in slugs, f"{target}: {rel} has no heading with slug {anchor!r}; it has {sorted(slugs)}"
    elif kind == "dir":
        assert path in _TRACKED_DIRS, f"{target}: {path!r} is not a tracked directory"
    elif kind == "readme-anchor":
        slugs = _slugs(_README)
        assert path in slugs, f"{target}: {_README} has no heading with slug {path!r}; it has {sorted(slugs)}"
    elif kind == "workflow":
        assert f".github/workflows/{path}" in _TRACKED, f"{target}: no workflow file {path!r}"
    elif kind == "pypi":
        assert path == f"/project/{_PROJECT['name']}/", f"{target} is not this distribution's page"
    else:
        assert kind in {"releases", "external"}


@pytest.mark.parametrize(("text", "target"), _LINKS, ids=_IDS)
def test_link_text_that_is_a_path_matches_its_target(text: str, target: str):
    """Both ways: text that names a tracked path must link to that path, and a
    file or directory link whose text reads as a path must name that path (the
    full path, or the file's own name, as `ci.yml`)."""
    kind, path = _classify(target)
    plain = text.strip("`")
    if plain in _TRACKED or plain in _TRACKED_DIRS:
        assert kind in {"file", "dir"}, f"[{text}] names a tracked path but links to {target}"
        assert path.partition("#")[0] == plain, f"[{text}] links to {path.partition('#')[0]!r} instead"
    if kind in {"file", "dir"} and _PATH_TEXT.fullmatch(plain):
        rel = path.partition("#")[0]
        assert plain in {rel, Path(rel).name}, f"[{text}] reads as a path but links to {rel!r}"


def test_the_documentation_index_lists_every_docs_page():
    """`## documentation` names every top-level `docs/*.md`, or `_NOT_IN_INDEX` does."""
    section = re.search(r"^## documentation\s*$(.*?)(?=^## |\Z)", _prose(_README), re.M | re.S)
    assert section, f"{_README} has no `## documentation` section"
    listed = {_classify(target)[1] for _, target in _MD_LINK.findall(section.group(1))}
    pages = {p for p in _TRACKED if re.fullmatch(r"docs/[^/]+\.md", p)}
    assert pages, "docs/ has no pages"
    stale = _NOT_IN_INDEX - pages
    assert not stale, f"_NOT_IN_INDEX names pages that no longer exist: {sorted(stale)}"
    assert not listed & _NOT_IN_INDEX, f"listed and exempt at once: {sorted(listed & _NOT_IN_INDEX)}"
    missing = pages - listed - _NOT_IN_INDEX
    assert not missing, (
        f"{_README}'s `## documentation` list omits {sorted(missing)}. "
        "Add the page there, or to _NOT_IN_INDEX in tests/test_readme_links.py with the reason."
    )
