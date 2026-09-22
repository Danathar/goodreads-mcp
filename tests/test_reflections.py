"""`docs/reflections/` makes checkable claims about this codebase; these check them.

Nothing read this directory before. `grep -rF` for either full path
(`docs/reflections/README.md`, `docs/reflections/2026-09-verification.md`)
over `tests/` returned nothing, and no test mentioned the word "reflection" at
all. Full paths, not basenames: `.claude/README.md` and
`.claude/memory/README.md` are both read by tests, so a `README.md` match
proves nothing about this one.

That matters more here than for most docs. `README.md` says of the entries
beside it: "Delete entries that stop being true." It is the only enforcement
the layer has, and it is a sentence.

One claim had already stopped being true. `2026-09-verification.md` is an
essay about documentation that asserts things the source contradicts, and its
own second lesson said, in the present tense, that `client.py`'s docstring
"says four unofficial-but-stable read surfaces". It says five, and has since
#82 (issue #81) landed after the reflection was written in #52 — so the entry
had become an instance of the failure it was written to warn about. The text
now states that in the past tense, and
`test_a_present_tense_quote_of_the_client_docstring_is_really_in_it` below is
what keeps the tense honest: attribute a quote to `client.py`'s docstring with
"says" and it must be in the docstring; attribute one with "said" and it must
not still be there, or the past tense is the lie instead.

The rest of the entry's claims were true when this file was written and
nothing held them there. They are pinned the same way the other document
tests in this repo pin theirs — **read out of the source, not restated**:

* The call-site count for `_paginated_graphql_edges` is parsed out of the
  entry's own prose ("has five call sites") as a number *word*, and compared
  against call sites counted from `server.py`'s AST. Add a sixth caller and
  the entry fails until its sentence is updated.
* The two functions the entry names as legitimately hand-paginating are read
  out of that same sentence, then checked both ways: they must not call the
  helper, and they must walk page tokens themselves.
* `list_shelves` must still reach HTML with a regex, `.claude/settings.json`
  must still gate both `Edit` and `Write` on `.github/workflows/**` (the
  bullet's whole point is that gating one and not the other was the defect),
  and `GoodreadsClient.graphql_config` must exist, because the skill snippet
  the entry vouches for calls it.

Finally, both files are checked for reachability: every backticked repo path
resolves, every backticked identifier is defined in the package, and
`README.md`'s layer table links a file that exists for each of the four
layers it describes.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_REFLECTIONS = _ROOT / "docs" / "reflections"
_README = _REFLECTIONS / "README.md"
_ENTRY = _REFLECTIONS / "2026-09-verification.md"
_PACKAGE = _ROOT / "goodreads_mcp"
_SERVER = _PACKAGE / "server.py"
_CLIENT = _PACKAGE / "client.py"
_SETTINGS = _ROOT / ".claude" / "settings.json"

_PAGINATION_HELPER = "_paginated_graphql_edges"
_WORKFLOW_GLOB = "./.github/workflows/**"

# `[`AGENTS.md`](../../AGENTS.md)` — the link target, not the label.
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
# An entry filename is dated: `2026-09-verification.md`.
_ENTRY_NAME = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})(?:-\d{2})?-(?P<theme>[a-z0-9-]+)\.md$")
_DATE_FIELD = re.compile(r"^\*\*Date:\*\*\s*(?P<date>\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
_FROM_FIELD = re.compile(r"^\*\*From:\*\*\s*(?P<from>\S.*\S)\s*$", re.MULTILINE)
# "has five call sites" — the count as the entry writes it.
_CALL_SITES = re.compile(r"has (?P<count>[a-z]+) call sites")
# "`get_reviews` and `popular_books` legitimately paginate by hand"
_BY_HAND = re.compile(r"`(?P<first>\w+)` and `(?P<second>\w+)` legitimately paginate by hand")
# "`client.py`'s docstring says \"four unofficial-but-stable read surfaces\""
_DOCSTRING_QUOTE = re.compile(r"`client\.py`'s docstring (?P<verb>says|said) \"(?P<quote>[^\"]+)\"")
# The module docstring's own numbered surface list: "  1. Shelf RSS feeds  — ..."
_SURFACE_ITEM = re.compile(r"^\s+(?P<index>\d+)\.\s+\S", re.MULTILINE)
_SURFACE_COUNT_PHRASE = re.compile(r"on (?P<count>[a-z]+) unofficial-but-stable read surfaces")

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# Backticked tokens in these files that name neither a repo path nor a Python
# identifier: agent-tool names, a GraphQL input type, a page-embedded global,
# a shell one-liner, a skill directory. Each is asserted to still exist in its
# own home below rather than merely being waved past here.
_NOT_A_PATH_OR_SYMBOL = {
    "Edit",
    "Write",
    "PaginationInput",
    "__NEXT_DATA__",
    "check-live-endpoints",
    "grep -n X",
    "raise_for_status()",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _squash(text: str) -> str:
    """Markdown hard-wraps. A sentence this file matches as a phrase is split
    across lines in the source, so collapse runs of whitespace before matching
    anything longer than a word."""
    return re.sub(r"\s+", " ", text)


def _entry_files() -> list[Path]:
    return sorted(p for p in _REFLECTIONS.glob("*.md") if p.name != "README.md")


def _number(word: str, where: str) -> int:
    assert word in _NUMBER_WORDS, f"{where}: {word!r} is not a number word this test knows"
    return _NUMBER_WORDS[word]


def _module_docstring(path: Path) -> str:
    doc = ast.get_docstring(ast.parse(_read(path)))
    assert doc, f"{path.relative_to(_ROOT)} has no module docstring"
    return doc


def _function_defs(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(_read(path))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _calls_named(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and (
            (isinstance(call.func, ast.Name) and call.func.id == name)
            or (isinstance(call.func, ast.Attribute) and call.func.attr == name)
        )
    ]


@pytest.fixture(scope="module")
def readme() -> str:
    return _squash(_read(_README))


@pytest.fixture(scope="module")
def entry() -> str:
    return _squash(_read(_ENTRY))


@pytest.fixture(scope="module")
def server_defs() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return _function_defs(_SERVER)


# ------------------------------------------------------------------ the layer


def test_the_directory_holds_a_readme_and_at_least_one_entry():
    assert _README.is_file(), "docs/reflections/README.md is gone"
    entries = _entry_files()
    assert entries, "docs/reflections/ has a README describing entries and no entries"


def test_every_layer_table_link_resolves(readme):
    """The table tells an agent where each kind of knowledge goes. A dead link
    there sends durable knowledge into a file that does not exist."""
    targets = _MD_LINK.findall(readme)
    assert targets, "README.md's layer table links nothing"
    for target in targets:
        resolved = (_REFLECTIONS / target).resolve()
        assert resolved.exists(), f"README.md links {target}, which does not exist"


def test_the_layer_table_names_every_other_layer(readme):
    """Four layers, four rows. Drop a layer's row and entries start landing
    here by default, which is exactly what the table exists to prevent."""
    linked = {
        (_REFLECTIONS / target).resolve()
        for target in _MD_LINK.findall(readme)
    }
    for expected in (
        _ROOT / "AGENTS.md",
        _ROOT / ".claude" / "memory" / "corrections.md",
        _ROOT / ".claude" / "session-summary.md",
    ):
        assert expected.resolve() in linked, (
            f"README.md's layer table does not link {expected.relative_to(_ROOT)}"
        )
    assert "this directory" in readme, (
        "README.md's layer table no longer names this directory as a layer"
    )


def test_every_entry_filename_is_dated_and_themed():
    """"One file per theme, dated." — README.md."""
    for path in _entry_files():
        assert _ENTRY_NAME.match(path.name), (
            f"{path.name} is not `<date>-<theme>.md`; README.md requires one file "
            "per theme, dated"
        )


def test_every_entry_filename_agrees_with_its_own_date_field():
    for path in _entry_files():
        name = _ENTRY_NAME.match(path.name)
        assert name, f"{path.name} is not a dated entry filename"
        field = _DATE_FIELD.search(_read(path))
        assert field, f"{path.name} has no `**Date:**` line"
        year, month, _ = field.group("date").split("-")
        assert (year, month) == (name.group("year"), name.group("month")), (
            f"{path.name} is filed under {name.group('year')}-{name.group('month')} "
            f"but dates itself {field.group('date')}"
        )


def test_every_entry_says_where_it_came_from():
    """A reflection with no provenance cannot be re-checked against the work
    that produced it."""
    for path in _entry_files():
        assert _FROM_FIELD.search(_read(path)), f"{path.name} has no `**From:**` line"


# ------------------------------------------------- the entry's source claims


def test_the_call_site_count_the_entry_states_is_the_real_one(entry, server_defs):
    stated = _CALL_SITES.search(entry)
    assert stated, (
        "the entry no longer states how many call sites "
        f"`{_PAGINATION_HELPER}` has; its argument rests on that count"
    )
    expected = _number(stated.group("count"), "the entry's call-site count")
    tree = ast.parse(_read(_SERVER))
    helper = server_defs.get(_PAGINATION_HELPER)
    assert helper is not None, f"{_PAGINATION_HELPER} is gone from server.py"
    actual = [
        call.lineno
        for call in _calls_named(tree, _PAGINATION_HELPER)
        # The definition itself is not a call site; neither is a recursive one.
        if not (helper.lineno <= call.lineno <= (helper.end_lineno or helper.lineno))
    ]
    assert len(actual) == expected, (
        f"the entry says `{_PAGINATION_HELPER}` has {stated.group('count')} call "
        f"sites; server.py has {len(actual)}, at lines {actual}"
    )


def test_the_functions_the_entry_calls_hand_paginators_do_not_use_the_helper(entry, server_defs):
    named = _BY_HAND.search(entry)
    assert named, "the entry no longer names the functions that paginate by hand"
    for name in (named.group("first"), named.group("second")):
        node = server_defs.get(name)
        assert node is not None, f"the entry names `{name}`, which server.py does not define"
        assert not _calls_named(node, _PAGINATION_HELPER), (
            f"the entry says `{name}` paginates by hand, but it now calls "
            f"`{_PAGINATION_HELPER}`"
        )


def test_the_functions_the_entry_calls_hand_paginators_really_walk_page_tokens(entry, server_defs):
    """Not calling the helper is half the claim; the other half is that they
    paginate at all. A function that stopped paginating would pass the first
    check while making the entry's point about legitimate hand-rolling moot."""
    named = _BY_HAND.search(entry)
    assert named, "the entry no longer names the functions that paginate by hand"
    for name in (named.group("first"), named.group("second")):
        node = server_defs.get(name)
        assert node is not None, f"the entry names `{name}`, which server.py does not define"
        source = ast.get_source_segment(_read(_SERVER), node) or ""
        assert "pageInfo" in source and "nextPageToken" in source, (
            f"the entry says `{name}` paginates by hand, but its body walks no "
            "pageInfo/nextPageToken"
        )


def test_list_shelves_still_scrapes_html_with_a_regex(entry, server_defs):
    """The entry's first bullet turns on `list_shelves` being the one surface
    with no structured form. Give it a structured surface and the bullet is
    telling a future agent to preserve a scrape that is no longer needed."""
    assert "`list_shelves`" in entry, "the entry no longer names `list_shelves`"
    node = server_defs.get("list_shelves")
    assert node is not None, "server.py no longer defines list_shelves"
    assert _calls_named(node, "findall"), (
        "the entry says `list_shelves` regexes HTML; its body no longer calls re.findall"
    )


def test_the_permissions_file_gates_both_edit_and_write_on_workflows(entry):
    """"A permissions file gated `Edit` on workflows but not `Write`." That is
    the defect the bullet reports as found; nothing else pins the fix."""
    assert "`Edit`" in entry and "`Write`" in entry, (
        "the entry no longer names the Edit/Write permissions defect"
    )
    ask = json.loads(_read(_SETTINGS))["permissions"]["ask"]
    for tool in ("Edit", "Write"):
        assert f"{tool}({_WORKFLOW_GLOB})" in ask, (
            f".claude/settings.json does not gate {tool} on {_WORKFLOW_GLOB}; that is "
            "the exact defect docs/reflections/2026-09-verification.md reports as fixed"
        )


def test_the_skill_snippet_the_entry_vouches_for_still_calls_a_real_method(entry):
    """"Both the `graphql_config` snippet in `check-live-endpoints` and the
    settings hook were run before committing." Rename the method and the
    vouched-for snippet stops running."""
    assert "`graphql_config`" in entry, "the entry no longer names the graphql_config snippet"
    skill = _ROOT / ".claude" / "skills" / "check-live-endpoints" / "SKILL.md"
    assert skill.is_file(), "the entry names the check-live-endpoints skill, which is gone"
    assert "graphql_config" in _read(skill), (
        "the entry says the check-live-endpoints skill carries a graphql_config "
        "snippet; it does not"
    )
    assert "graphql_config" in _function_defs(_CLIENT), (
        "the snippet the entry vouches for calls GoodreadsClient().graphql_config, "
        "which client.py no longer defines"
    )


def test_a_present_tense_quote_of_the_client_docstring_is_really_in_it(entry):
    """The tense is the claim.

    "says X" asserts something about today's `client.py`; "said X" asserts the
    opposite, that X was corrected. Both are checkable, and this entry has
    already been wrong in the first direction: it said the docstring "says
    four unofficial-but-stable read surfaces" long after #82 made it five.
    """
    attributions = _DOCSTRING_QUOTE.findall(entry)
    assert attributions, (
        "the entry no longer quotes `client.py`'s docstring; the second lesson "
        "rests on that quote"
    )
    docstring = _module_docstring(_CLIENT)
    for verb, quote in attributions:
        if verb == "says":
            assert quote in docstring, (
                f"the entry says client.py's docstring says {quote!r}; it does not. "
                "Either the docstring changed and the entry needs the past tense, or "
                "the quote is wrong."
            )
        else:
            assert quote not in docstring, (
                f"the entry says client.py's docstring said {quote!r}, implying it no "
                "longer does — but it still does"
            )


def test_the_client_docstring_counts_its_own_surfaces_correctly():
    """The entry's lesson is that a docstring's own summary drifts from the list
    below it. This is that check, on the docstring the entry is about."""
    docstring = _module_docstring(_CLIENT)
    phrase = _SURFACE_COUNT_PHRASE.search(docstring)
    assert phrase, "client.py's docstring no longer says how many read surfaces it rides"
    stated = _number(phrase.group("count"), "client.py's surface count")
    listed = [int(m.group("index")) for m in _SURFACE_ITEM.finditer(docstring)]
    assert listed == list(range(1, len(listed) + 1)), (
        f"client.py's surface list is not numbered 1..n; it reads {listed}"
    )
    assert stated == len(listed), (
        f"client.py's docstring says {phrase.group('count')} read surfaces and then "
        f"lists {len(listed)}"
    )


def test_the_entry_names_the_fix_that_made_its_client_claim_history(entry):
    """A past-tense claim with no pointer to the fix cannot be re-checked. The
    docstring correction is issue #81 / PR #82."""
    assert re.search(r"#8[12]\b", entry), (
        "the entry states the client.py docstring defect in the past tense without "
        "naming #81/#82, the fix that made it past tense"
    )


# ------------------------------------------------------------- reachability


def test_every_backticked_repo_path_in_the_directory_resolves():
    for path in (_README, *_entry_files()):
        for token in _BACKTICKED.findall(_read(path)):
            if token in _NOT_A_PATH_OR_SYMBOL or not re.fullmatch(r"[\w./-]+\.\w+", token):
                continue
            if "/" in token:
                assert (_ROOT / token).exists(), (
                    f"{path.name} backticks `{token}`, which does not exist"
                )
                continue
            matches = [
                p
                for p in _ROOT.rglob(token)
                if not any(part in {".git", ".venv", "node_modules"} for part in p.parts)
            ]
            assert matches, f"{path.name} backticks `{token}`, which is nowhere in the repo"


def test_every_backticked_identifier_in_the_directory_is_defined_in_the_package():
    defined: set[str] = set()
    for module in sorted(_PACKAGE.glob("*.py")):
        defined |= set(_function_defs(module))
    for path in (_README, *_entry_files()):
        for token in _BACKTICKED.findall(_read(path)):
            if token in _NOT_A_PATH_OR_SYMBOL or not re.fullmatch(r"_?[a-z][a-z0-9_]*", token):
                continue
            assert token in defined, (
                f"{path.name} backticks `{token}` as a function of this package; "
                "goodreads_mcp defines no such function"
            )


def test_the_tokens_this_file_exempts_from_path_checking_are_all_still_real(entry, readme):
    """An exemption list is a second place claims can rot. Each entry is
    exempted because it names something other than a repo path — so each must
    still name that something."""
    text = f"{entry}\n{readme}"
    for token in _NOT_A_PATH_OR_SYMBOL:
        assert f"`{token}`" in text, (
            f"this test exempts `{token}` from the path check, but no reflection "
            "mentions it any more"
        )
    ask = json.loads(_read(_SETTINGS))["permissions"]["ask"]
    assert any(item.startswith("Edit(") for item in ask)
    assert any(item.startswith("Write(") for item in ask)
    server_source = _read(_SERVER)
    assert "PaginationInput" in server_source, (
        "the entry's `PaginationInput` claim names a shape server.py no longer sends"
    )
    assert "__NEXT_DATA__" in _read(_CLIENT), (
        "the entry's `__NEXT_DATA__` claim names a surface client.py no longer parses"
    )
    assert (_ROOT / ".claude" / "skills" / "check-live-endpoints").is_dir()
