"""Answer a fake GraphQL call with only the fields its query selects.

A real GraphQL server returns exactly the fields a query document asks for.
The offline fakes answer from hand-written fixtures instead, and a fixture
carries whatever fields its author typed. So a field deleted from one of the
`_Q_*` documents in `goodreads_mcp/server.py` -- `imageUrl` out of
`_Q_SIMILAR`, `ratingsCount` out of `_Q_AUTHOR` -- still arrived in the fake
response, the shaper still copied it, and the offline suite stayed green. Only
the nightly live suite could notice, a day after the merge, and its drift issue
blames Goodreads.

`prune(response, query)` closes that gap: it drops every key the query does not
select, at every depth, so a fake answers the way the server would. Nothing here
is a test.

The parser covers the GraphQL the `_Q_*` documents use: one operation, its
variable definitions, field arguments (which may hold `{ ... }` input objects),
nested selection sets, and inline fragments (`... on Type { ... }`). Anything
else -- named fragments, directives, aliases -- raises rather than being
misread as "selects nothing". An inline fragment applies to an object whose
`__typename` names its type; an object without one gets every fragment, since a
fixture cannot always say which member of a union it is.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r'\.\.\.|[{}():@$!=,\[\]]|"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_]*|-?\d+(?:\.\d+)?')


class Selection:
    """The fields one selection set asks for, and its inline fragments."""

    def __init__(self) -> None:
        self.fields: dict[str, Selection | None] = {}
        self.fragments: list[tuple[str, Selection]] = []

    def for_object(self, typename: Any) -> dict[str, Selection | None]:
        """The fields that apply to an object of `typename` (None if unknown)."""

        fragments = [sel for on, sel in self.fragments if on == typename]
        if not fragments:
            fragments = [sel for _, sel in self.fragments]
        fields = dict(self.fields)
        for fragment in fragments:
            for name, sub in fragment.for_object(typename).items():
                fields[name] = _merge(fields.get(name), sub)
        return fields


def _merge(a: Selection | None, b: Selection | None) -> Selection | None:
    if a is None or b is None:
        return a or b
    merged = Selection()
    merged.fields = dict(a.fields)
    for name, sub in b.fields.items():
        merged.fields[name] = _merge(merged.fields.get(name), sub)
    merged.fragments = a.fragments + b.fragments
    return merged


def _tokens(document: str) -> list[str]:
    tokens = _TOKEN.findall(document)
    if "".join(tokens) != re.sub(r"\s+", "", document):
        raise ValueError("query uses syntax this reader does not model")
    return tokens


def _skip_arguments(tokens: list[str], i: int) -> int:
    """Return the index just past the `(...)` group that starts at `i`."""

    depth = 0
    while True:
        token = tokens[i]
        depth += token == "("
        depth -= token == ")"
        i += 1
        if depth == 0:
            return i


def _selection_set(tokens: list[str], i: int) -> tuple[Selection, int]:
    """Parse the `{ ... }` that starts at `i`; return it and the index after."""

    if tokens[i] != "{":
        raise ValueError(f"expected '{{', found {tokens[i]!r}")
    selection = Selection()
    i += 1
    while tokens[i] != "}":
        if tokens[i] == "...":
            if tokens[i + 1] != "on":
                raise ValueError("named fragments are not modeled")
            on_type = tokens[i + 2]
            fragment, i = _selection_set(tokens, i + 3)
            selection.fragments.append((on_type, fragment))
            continue
        name = tokens[i]
        if not re.fullmatch(r"[A-Za-z_]\w*", name) or tokens[i + 1] in (":", "@"):
            raise ValueError(f"aliases and directives are not modeled: {name!r}")
        i += 1
        if tokens[i] == "(":
            i = _skip_arguments(tokens, i)
        sub = None
        if tokens[i] == "{":
            sub, i = _selection_set(tokens, i)
        selection.fields[name] = _merge(selection.fields.get(name), sub)
    return selection, i + 1


def selection(query: str) -> Selection:
    """The root selection set of `query`, a single anonymous or named operation."""

    tokens = _tokens(query)
    i = 0
    if tokens[i] in ("query", "mutation", "subscription"):
        i += 1
        if re.fullmatch(r"[A-Za-z_]\w*", tokens[i]) and tokens[i] != "{":
            i += 1
        if tokens[i] == "(":
            i = _skip_arguments(tokens, i)
    root, end = _selection_set(tokens, i)
    if end != len(tokens):
        raise ValueError("only one operation per document is modeled")
    return root


def prune(value: Any, sel: Selection | None) -> Any:
    """`value` with every key `sel` does not select removed, at every depth."""

    if sel is None:
        return value
    if isinstance(value, list):
        return [prune(item, sel) for item in value]
    if not isinstance(value, dict):
        return value
    fields = sel.for_object(value.get("__typename"))
    return {key: prune(item, fields[key]) for key, item in value.items() if key in fields}
