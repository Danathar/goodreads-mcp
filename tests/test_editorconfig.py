"""`.editorconfig` governs every tracked file; these make the tree obey it.

The file declares the charset, line ending, final newline, trailing-whitespace
and indentation rules for every tracked file, and until now **nothing read it**
— not CI, not a test, and not a linter, because this repository configures none
(no ruff, black, flake8 or prettier). An editor without EditorConfig
support, a generated file, or a commit made through the GitHub web UI could
introduce a CRLF, a tab indent, a missing final newline or a trailing space and
nothing would go red.

The tree is fully conformant at the revision that added this file, so this is a
guard added while it is still green: no file needed reformatting to make it
pass.

Three kinds of check, and the split matters:

* **The config is parsed, not restated.** `_parse` reads the sections in order
  and `_resolve` applies them the way EditorConfig specifies — later section
  wins, a glob with no `/` matches at any depth, `{a,b}` is alternation. The
  rules enforced below are read out of the parsed result. Change `indent_size`
  for `*.py` to 2 and these tests demand 2-column Python blocks; they do not
  quietly keep checking for 4.
* **The tree is checked against what was parsed**, file by file, over
  `git ls-files` rather than a hand-written list, so a new file is covered the
  moment it is tracked.
* **The config is checked against the tree.** A section matching nothing, a
  property no test enforces, or a file type with no recorded indent decision
  all fail. That is the half that keeps the file from rotting into decoration:
  a rule nobody obeys and nobody checks is indistinguishable from a comment.

The one place a naive check would have been wrong is `indent_size`. Scanning
for "every indented line is a multiple of N" reports 213 false violations on
this tree — continuation lines, wrapped docstring bullets and hanging indents
are not block indentation and are not what `indent_size` describes. Python is
therefore checked through the AST, where a block's indentation is exact, with
`elif` carved out explicitly because it is the one construct whose body starts
at its parent's column. Data files keep the multiple-of-N rule, which is
accurate for them, with YAML block scalars skipped: the body of a `run: |` is
shell, not YAML, and its indentation means nothing here.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / ".editorconfig"

# The properties these tests know how to enforce, against the check that does
# it. Asserted exhaustive in both directions: a property added to
# `.editorconfig` without a check here fails, and so does a check here for a
# property the file stopped declaring. Six rules is the whole file; the point
# of the table is that it cannot silently become five or seven.
_ENFORCED = {
    "charset": "every tracked file decodes as that charset, and carries no BOM",
    "end_of_line": "no tracked file contains the other ending",
    "insert_final_newline": "every non-empty tracked file ends with a newline",
    "trim_trailing_whitespace": "no line of a trimmed file ends in a space or tab",
    "indent_style": "no line is indented with a tab",
    "indent_size": "Python block bodies by AST; data files by leading-space width",
}

# The values the checks below are written for. Kept separate from `_ENFORCED`
# because changing a value is a legitimate decision that needs the checks
# rewritten, not a property disappearing: `charset = utf-16` would make the
# decode assertion wrong rather than stale, and it would still pass, since a
# UTF-8 file is not required to be invalid UTF-16 to be a violation.
_IMPLEMENTED_VALUES = {
    "charset": "utf-8",
    "end_of_line": "lf",
    "indent_style": "space",
}

# Every file suffix in the tree, against the indent width `.editorconfig`
# resolves for it. A decision log: a new kind of file cannot enter the tree
# without someone saying which column it indents at. `""` is the extensionless
# group (`LICENSE`, `.gitignore`, `.mcpbignore`, `.editorconfig` — a leading dot
# is not a suffix), and `.MIT` is `LICENSE.MIT`. Both fall through to `[*]`.
_INDENT_BY_SUFFIX = {
    "": 4,
    ".MIT": 4,
    ".json": 2,
    ".md": 4,
    ".mdc": 4,
    ".py": 4,
    ".toml": 2,
    ".yml": 2,
}

# Suffixes whose indentation is a leading-space width rather than a block
# structure. `.md` is deliberately absent: a Markdown list indents by its
# marker's width, which is not `indent_size` and never was.
_DATA_SUFFIXES = {".json", ".toml", ".yml", ".yaml"}

# `key: |`, `key: >-`, `- key: |2` — a YAML block scalar. Everything indented
# further than the introducing line is opaque content, not YAML.
_BLOCK_SCALAR = re.compile(r":\s*[|>][+-]?\d*\s*(#.*)?$")


# --------------------------------------------------------------------------
# Parsing `.editorconfig`
# --------------------------------------------------------------------------


def _parse(text: str) -> tuple[dict[str, str], list[tuple[str, dict[str, str]]]]:
    """Return the preamble properties and the `[glob]` sections, in file order.

    Hand-rolled rather than `configparser`, for two reasons: `root = true` sits
    before any section, which `configparser` rejects outright, and order is
    load-bearing here — EditorConfig resolves a property by letting the last
    matching section win.
    """
    preamble: dict[str, str] = {}
    sections: list[tuple[str, dict[str, str]]] = []
    current = preamble
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {}
            sections.append((line[1:-1], current))
            continue
        key, separator, value = line.partition("=")
        assert separator, f".editorconfig line {number} is neither a section nor a property: {raw!r}"
        current[key.strip().lower()] = value.strip().lower()
    return preamble, sections


def _expand_braces(glob: str) -> list[str]:
    """`*.{yml,json}` -> `['*.yml', '*.json']`, for the one brace form in use.

    EditorConfig also has `{a..b}` numeric ranges and nested groups. Neither
    appears here, and guessing at them would mean a translator whose behaviour
    nothing in this repository exercises, so they are refused loudly instead.
    """
    start = glob.find("{")
    if start == -1:
        return [glob]
    end = glob.find("}", start)
    assert end != -1, f"unbalanced brace group in glob {glob!r}"
    inner = glob[start + 1 : end]
    assert "{" not in inner, f"nested brace groups are not supported: {glob!r}"
    assert ".." not in inner, f"numeric range globs are not supported: {glob!r}"
    return [
        expanded
        for alternative in inner.split(",")
        for expanded in _expand_braces(glob[:start] + alternative + glob[end + 1 :])
    ]


def _translate(glob: str) -> re.Pattern[str]:
    """One EditorConfig glob (already brace-expanded) as a path regex.

    `*` stops at a separator and `**` crosses it, which is why this is not
    `fnmatch`: `fnmatch.translate('*.md')` matches `docs/x.md`, and would make
    the sections indistinguishable from each other on a nested tree.
    """
    out: list[str] = []
    index = 0
    while index < len(glob):
        if glob.startswith("**", index):
            out.append(".*")
            index += 2
        elif glob[index] == "*":
            out.append("[^/]*")
            index += 1
        elif glob[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(glob[index]))
            index += 1
    body = "".join(out)
    if "/" not in glob:
        # A glob without a separator matches the name at any depth.
        body = "(?:.*/)?" + body
    return re.compile("^" + body + "$")


_PREAMBLE, _SECTIONS = _parse(_CONFIG.read_text(encoding="utf-8"))
_MATCHERS = [
    (glob, [_translate(expanded) for expanded in _expand_braces(glob)], properties)
    for glob, properties in _SECTIONS
]


def _resolve(path: str) -> dict[str, str]:
    """The properties EditorConfig gives `path`, later sections winning."""
    resolved: dict[str, str] = {}
    for _, patterns, properties in _MATCHERS:
        if any(pattern.match(path) for pattern in patterns):
            resolved.update(properties)
    assert resolved, f"{path} matches no section of .editorconfig, so nothing governs it"
    return resolved


def _matching(glob: str, patterns: list[re.Pattern[str]]) -> list[str]:
    return [path for path in _TRACKED if any(pattern.match(path) for pattern in patterns)]


def _is_true(path: str, key: str) -> bool:
    value = _resolve(path).get(key)
    assert value in {"true", "false"}, f"{key} for {path} is {value!r}, not a boolean"
    return value == "true"


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------


def _tracked_files() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(path for path in listing.stdout.split("\0") if path)


_TRACKED = _tracked_files()
_PYTHON = [path for path in _TRACKED if path.endswith(".py")]
_DATA = [path for path in _TRACKED if Path(path).suffix in _DATA_SUFFIXES]


def _raw(path: str) -> bytes:
    return (_ROOT / path).read_bytes()


def _lines(path: str) -> list[str]:
    return _raw(path).decode("utf-8").splitlines()


def test_the_file_list_is_not_empty():
    """Every check below walks this list, so an empty one would pass vacuously."""
    assert len(_TRACKED) > 50, f"git ls-files returned {len(_TRACKED)} paths, which cannot be this repository"


def test_root_is_declared():
    """Without `root = true` an .editorconfig further up the filesystem also applies.

    That would make the rules checked here depend on where the checkout lives,
    which is exactly the property a test cannot have.
    """
    assert _PREAMBLE.get("root") == "true", (
        f".editorconfig does not set `root = true` before its first section; preamble is {_PREAMBLE!r}"
    )


def test_every_property_declared_is_one_these_tests_enforce():
    declared = {key for _, properties in _SECTIONS for key in properties}
    unenforced = declared - set(_ENFORCED)
    assert not unenforced, (
        f".editorconfig declares {sorted(unenforced)}, which nothing here checks. "
        "Add the check and a row to _ENFORCED, or the rule is decoration."
    )


def test_every_property_these_tests_enforce_is_still_declared():
    declared = {key for _, properties in _SECTIONS for key in properties}
    missing = set(_ENFORCED) - declared
    assert not missing, (
        f"_ENFORCED claims to check {sorted(missing)}, but .editorconfig no longer declares it; "
        "the checks below are asserting a rule that was dropped"
    )


@pytest.mark.parametrize("key", sorted(_IMPLEMENTED_VALUES))
def test_the_checks_are_written_for_the_values_still_declared(key: str):
    """A changed value needs a rewritten check, not a stale one that still passes."""
    expected = _IMPLEMENTED_VALUES[key]
    actual = _PREAMBLE.get(key) or _resolve("goodreads_mcp/server.py").get(key)
    assert actual == expected, (
        f".editorconfig now sets {key} = {actual!r}; the checks in this file are written for "
        f"{expected!r} and would pass without testing the new rule. Rewrite them."
    )


@pytest.mark.parametrize("glob", [glob for glob, _ in _SECTIONS])
def test_every_section_still_matches_a_tracked_file(glob: str):
    """A section for a file type the repository no longer has governs nothing."""
    patterns = [_translate(expanded) for expanded in _expand_braces(glob)]
    assert _matching(glob, patterns), (
        f".editorconfig section [{glob}] matches no tracked file. Either the rule is dead "
        "and should go, or the paths it was written for moved."
    )


def test_the_suffix_table_covers_the_tree_in_both_directions():
    """Every kind of file in the tree has a recorded indent decision, and vice versa."""
    present = {Path(path).suffix for path in _TRACKED}
    assert present == set(_INDENT_BY_SUFFIX), (
        f"tracked suffixes are {sorted(present)} but _INDENT_BY_SUFFIX records "
        f"{sorted(_INDENT_BY_SUFFIX)}. A new file type needs a decision about the column "
        "it indents at; a suffix that left needs its row removed."
    )


@pytest.mark.parametrize("suffix", sorted(_INDENT_BY_SUFFIX))
def test_the_config_resolves_the_recorded_indent_for_every_suffix(suffix: str):
    """Pins the resolver itself, not just the files: `*.md` must not swallow `.mdc`."""
    sample = next(path for path in _TRACKED if Path(path).suffix == suffix)
    resolved = _resolve(sample)["indent_size"]
    assert resolved == str(_INDENT_BY_SUFFIX[suffix]), (
        f"{sample} resolves to indent_size {resolved}, but _INDENT_BY_SUFFIX records "
        f"{_INDENT_BY_SUFFIX[suffix]} for {suffix or 'extensionless files'}"
    )


def test_markdown_is_the_only_type_exempt_from_trimming():
    """The carve-out is real, and it is the only one.

    Trailing whitespace is a line break in Markdown, which is why the exemption
    exists. It is also the rule most easily widened by accident — a section
    added below `[*.md]` inherits nothing, but a `[*]` edit turns trimming off
    everywhere — so the exemption is asserted to be exactly Markdown-shaped.
    """
    exempt = {Path(path).suffix for path in _TRACKED if not _is_true(path, "trim_trailing_whitespace")}
    assert exempt == {".md"}, (
        f"trim_trailing_whitespace is off for {sorted(exempt)}; it should be off for Markdown only"
    )


# --------------------------------------------------------------------------
# The tree obeys the config
# --------------------------------------------------------------------------


def test_every_file_is_utf8_without_a_byte_order_mark():
    undecodable = []
    with_bom = []
    for path in _TRACKED:
        raw = _raw(path)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            undecodable.append(f"{path} ({exc})")
            continue
        if raw.startswith(b"\xef\xbb\xbf"):
            with_bom.append(path)
    assert not undecodable, f"not valid UTF-8: {undecodable}"
    assert not with_bom, f"{with_bom} start with a UTF-8 BOM; `charset = utf-8` means the bare encoding"


def test_no_file_uses_a_carriage_return():
    offenders = [path for path in _TRACKED if b"\r" in _raw(path)]
    assert not offenders, f"{offenders} contain a carriage return; `end_of_line = lf`"


def test_every_non_empty_file_ends_with_a_newline():
    # `goodreads_mcp/__init__.py` is empty; there is no final line to end.
    offenders = [path for path in _TRACKED if _raw(path) and not _raw(path).endswith(b"\n")]
    assert not offenders, f"{offenders} have no final newline; `insert_final_newline = true`"


def test_no_trailing_whitespace_where_the_config_says_to_trim():
    offenders = {}
    for path in _TRACKED:
        if not _is_true(path, "trim_trailing_whitespace"):
            continue
        lines = [number for number, line in enumerate(_lines(path), 1) if line != line.rstrip()]
        if lines:
            offenders[path] = lines[:5]
    assert not offenders, (
        f"trailing whitespace at {offenders}; `trim_trailing_whitespace = true` applies to those files"
    )


def test_no_line_is_indented_with_a_tab():
    offenders = {}
    for path in _TRACKED:
        lines = [number for number, line in enumerate(_lines(path), 1) if re.match(r"^ *\t", line)]
        if lines:
            offenders[path] = lines[:5]
    assert not offenders, f"tab indentation at {offenders}; `indent_style = space`"


# --------------------------------------------------------------------------
# indent_size, exactly
# --------------------------------------------------------------------------


def _block_indents(source: str) -> tuple[list[tuple[int, int]], list[int]]:
    """Every block body's indentation relative to its header, plus the elifs skipped.

    `elif` is the one construct whose body does not sit right of its header:
    the parser models it as an `If` inside the previous `If`'s `orelse`, at the
    same column. Carved out by shape — a lone `If` at the parent's column — and
    the caller checks each carve-out really is spelled `elif`, so an ordinary
    `else:` holding one `if` cannot hide inside the exemption.
    """
    tree = ast.parse(source)
    deltas: list[tuple[int, int]] = []
    elifs: list[int] = []
    for node in ast.walk(tree):
        if not hasattr(node, "col_offset"):
            continue
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list) or not block:
                continue
            first = block[0]
            if not hasattr(first, "col_offset"):
                continue
            if (
                field == "orelse"
                and len(block) == 1
                and isinstance(first, ast.If)
                and first.col_offset == node.col_offset
            ):
                elifs.append(first.lineno)
                continue
            deltas.append((first.lineno, first.col_offset - node.col_offset))
    return deltas, elifs


def test_python_blocks_indent_by_exactly_the_declared_size():
    offenders = {}
    for path in _PYTHON:
        size = int(_resolve(path)["indent_size"])
        deltas, _ = _block_indents((_ROOT / path).read_text(encoding="utf-8"))
        wrong = [(lineno, delta) for lineno, delta in deltas if delta != size]
        if wrong:
            offenders[path] = wrong[:5]
    assert not offenders, f"block bodies not indented by the declared size at {offenders}"


def test_nothing_hides_inside_the_elif_carve_out():
    """An ordinary `else:` holding a single `if` must not borrow the exemption."""
    for path in _PYTHON:
        source = (_ROOT / path).read_text(encoding="utf-8")
        lines = source.splitlines()
        for lineno in _block_indents(source)[1]:
            assert lines[lineno - 1].lstrip().startswith("elif"), (
                f"{path}:{lineno} was skipped as an `elif` but is not one; the carve-out is hiding "
                "a block that starts at its parent's column"
            )


def test_the_python_indent_check_actually_inspected_blocks():
    """Guards the check above against an AST walk that finds nothing to measure."""
    counted = [_block_indents((_ROOT / path).read_text(encoding="utf-8")) for path in _PYTHON]
    total = sum(len(deltas) for deltas, _ in counted)
    assert total > 500, f"only {total} Python blocks were measured; the walk is missing bodies"
    carved = sum(len(elifs) for _, elifs in counted)
    assert carved, "no `elif` in the tree, so the carve-out in _block_indents is unexercised"


def _significant_indents(path: str) -> list[tuple[int, int]]:
    """Leading-space widths, with YAML block-scalar bodies skipped.

    The body of a `run: |` is shell: its indentation is part of the script and
    says nothing about the document's. Without this, `ai-fix.yml`'s 19-column
    continuation inside a `run:` reads as a YAML indent violation.
    """
    widths: list[tuple[int, int]] = []
    opaque_beyond: int | None = None
    for number, line in enumerate(_lines(path), 1):
        if not line.strip():
            continue
        width = len(line) - len(line.lstrip(" "))
        if opaque_beyond is not None:
            if width > opaque_beyond:
                continue
            opaque_beyond = None
        widths.append((number, width))
        if _BLOCK_SCALAR.search(line):
            opaque_beyond = width
    return widths


def test_data_files_indent_in_multiples_of_the_declared_size():
    offenders = {}
    for path in _DATA:
        size = int(_resolve(path)["indent_size"])
        wrong = [(number, width) for number, width in _significant_indents(path) if width % size]
        if wrong:
            offenders[path] = wrong[:5]
    assert not offenders, f"data files not indented in multiples of the declared size at {offenders}"


def test_the_data_indent_check_has_something_to_measure():
    """`pyproject.toml` has no indented line at all; the set must not be all of them."""
    indented = sum(1 for path in _DATA for _, width in _significant_indents(path) if width)
    assert indented > 100, f"only {indented} indented data-file lines were measured"


def test_block_scalar_bodies_are_the_only_thing_skipped():
    """The skip is real (it fires) and narrow (it does not swallow the document).

    A too-eager block-scalar regex would silently exempt whole workflows from
    the indent check, which is the failure mode that leaves this test green
    while checking nothing.
    """
    workflow = ".github/workflows/ai-fix.yml"
    assert workflow in _TRACKED, "the workflow this check is anchored to has moved"
    measured = {number for number, _ in _significant_indents(workflow)}
    total = len([line for line in _lines(workflow) if line.strip()])
    assert len(measured) < total, "no line was skipped, so the block-scalar rule never fired"
    assert len(measured) > total // 2, (
        f"{len(measured)} of {total} lines survived the block-scalar skip; the regex is swallowing "
        "the document, not just the script bodies"
    )
