"""Join `docs/SECURITY-AI.md` to the guard, the rules and the files it describes.

Three tests read the page, all as a *source*: `test_agent_permissions.py` looks
for three phrases about the prefix limit, and `test_ai_fix_workflow.py` looks
for the word `pull_request_target`. Nothing read what the page says the guard
hook *does*, and that is where it had drifted. The hook grew through #115, #120
and #125 while the page, and the `.claude/README.md` section it points to,
stayed where they were:

* both said the guard denies `--no-index` and `--output` on `git diff` and
  `git log`, while `_GIT_DENIED` also holds `--output-file` and `--orderfile`,
  and `_GIT_DENIED_SHORT` refuses `-O`;
* neither said the guard refuses a `git diff` operand outside the checkout --
  `git diff .env /etc/hostname`, the one spelling that reads `.env` with no
  option on the line;
* the page listed three constructs the guard used to read differently from the
  shell ("Two constructs ... A third"), and the README said "Two ways ... both
  closed" over three bullets, while the guard's own docstring records six:
  `noglob`, the outside operand and a leading redirection were never written
  down on either page.

So the divergence list is joined three ways here -- the guard docstring's
bullets, the page's numbered list, the README's bullets -- by one exemplar
command per construct, which must appear in all three at the same position
and which `decide()` must refuse. The option list is read out of both pages and
set-compared to the guard's tables, and the rest of the page's checkable
claims are joined to the file each one names.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DOC = _ROOT / "docs" / "SECURITY-AI.md"
_README = _ROOT / ".claude" / "README.md"
_GUARD = _ROOT / ".claude" / "hooks" / "guard-bash.py"
_SETTINGS = _ROOT / ".claude" / "settings.json"
_RISK_TIERS = _ROOT / "docs" / "risk-tiers.md"
_LABELER = _ROOT / ".github" / "workflows" / "labeler.yml"

_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}

# One command per construct the guard used to read differently from the shell,
# in the order the guard's docstring records them. Each must be quoted in the
# matching guard bullet, page item and README bullet, and refused by `decide()`.
_DIVERGENCES = [
    "pytest --ignore=z#z /tmp/evil.py",  # `#` is not a comment mid-word
    "git diff --no-inde{x,x} a b",  # brace expansion
    "GIT_EXTERNAL_DIFF=prog git diff HEAD~1 HEAD",  # an assignment before the verb (#115)
    "noglob pytest -p evil",  # a wrapper the matcher steps over
    "git diff .env /etc/hostname",  # an operand outside the checkout
    ">out git diff HEAD",  # a redirection in front of the command name
]


def _squash(text: str) -> str:
    """Markdown and docstrings wrap; a phrase match must not care where."""
    return re.sub(r"\s+", " ", text)


def _backticked(text: str) -> list[str]:
    return re.findall(r"`([^`]+)`", _squash(text))


def _between(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head : text.index(end, head)]


def _load_guard():
    spec = importlib.util.spec_from_file_location("guard_bash_doc", _GUARD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard():
    return _load_guard()


@pytest.fixture(scope="module")
def doc() -> str:
    return _DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readme() -> str:
    return _README.read_text(encoding="utf-8")


def _settings() -> dict[str, list[str]]:
    return json.loads(_SETTINGS.read_text(encoding="utf-8"))["permissions"]


# ----------------------------------------------- where each list lives


def _guard_divergence_bullets(guard) -> list[str]:
    section = _between(
        guard.__doc__,
        "The guard only holds while the string it reads is the string the shell runs.",
        "decided as *not reachable*",
    )
    return [_squash(b) for b in re.split(r"^\* ", section, flags=re.M)[1:]]


def _doc_divergence_section(doc: str) -> str:
    return _between(doc, "carries a second failure mode", "Any new construct the guard resolves")


def _doc_divergence_items(doc: str) -> list[str]:
    section = _doc_divergence_section(doc)
    return [_squash(i) for i in re.split(r"^\d+\. ", section, flags=re.M)[1:]]


def _readme_divergence_section(readme: str) -> str:
    return _between(readme, "### The guard only holds if it reads what the shell runs", "The general shape")


def _readme_divergence_bullets(readme: str) -> list[str]:
    section = _readme_divergence_section(readme)
    return [_squash(b) for b in re.split(r"^- \*\*", section, flags=re.M)[1:]]


def _doc_guard_paragraph(doc: str) -> str:
    return _squash(_between(doc, "That hook is [`.claude/hooks/guard-bash.py`]", "carries a second failure mode"))


def _readme_guard_section(readme: str) -> str:
    return _squash(_between(readme, "## The guard hook", "### The guard only holds"))


# ----------------------------------------------- the divergence list, three ways


def test_the_exemplar_table_matches_the_guards_own_record(guard):
    """One exemplar per guard bullet, in order, each quoted in its own bullet.

    The guard's docstring is the primary record. A bullet added there without
    a row here fails, and so does a row that drifts to the wrong bullet.
    """
    bullets = _guard_divergence_bullets(guard)
    assert len(bullets) == len(_DIVERGENCES), [b[:60] for b in bullets]
    for exemplar, bullet in zip(_DIVERGENCES, bullets):
        assert f"`{exemplar}`" in bullet, (exemplar, bullet[:120])


@pytest.mark.parametrize("exemplar", _DIVERGENCES)
def test_every_exemplar_is_refused_by_the_guard(guard, exemplar: str):
    """A page that quotes a bypass as closed is wrong if the guard lets it through."""
    assert guard.decide(exemplar, cwd=_ROOT), f"the guard now allows {exemplar!r}"


def test_the_page_lists_every_construct_in_the_guards_order(doc: str):
    items = _doc_divergence_items(doc)
    assert len(items) == len(_DIVERGENCES), [i[:60] for i in items]
    for exemplar, item in zip(_DIVERGENCES, items):
        assert f"`{exemplar}`" in item, (exemplar, item[:120])


def test_the_readme_lists_every_construct_in_the_guards_order(readme: str):
    bullets = _readme_divergence_bullets(readme)
    assert len(bullets) == len(_DIVERGENCES), [b[:60] for b in bullets]
    for exemplar, bullet in zip(_DIVERGENCES, bullets):
        assert f"`{exemplar}`" in bullet, (exemplar, bullet[:120])


def test_the_page_counts_the_constructs_it_lists(doc: str):
    """ "Two constructs ... A third" is how the page stayed at three."""
    section = _squash(_doc_divergence_section(doc))
    counts = re.findall(r"\b(\w+) constructs broke that\b", section, flags=re.I)
    assert counts, "the page no longer says how many constructs broke the guard"
    assert [_NUMBER_WORDS[c.lower()] for c in counts] == [len(_DIVERGENCES)]
    assert not re.search(r"\bA (third|fourth|fifth|sixth)\b", section), "an ordinal item outside the list"


def test_the_readme_counts_the_ways_it_lists(readme: str):
    """It said "Two ways ... both closed" over three bullets."""
    section = _squash(_readme_divergence_section(readme))
    words = re.findall(r"\b(\w+) ways that came apart\b", section, flags=re.I)
    words += re.findall(r"\ball (\w+) closed\b", section, flags=re.I)
    assert len(words) == 2, words
    assert {_NUMBER_WORDS[w.lower()] for w in words} == {len(_DIVERGENCES)}
    assert "both closed" not in section


def test_the_guard_counts_the_ways_it_lists(guard):
    """It said "Three ways that used to come apart" over six bullets (#140)."""
    section = _squash(_between(guard.__doc__, "The guard only holds", "decided as *not reachable*"))
    words = re.findall(r"\b(\w+) ways that used to come apart\b", section, flags=re.I)
    assert words, "the guard docstring no longer says how many ways came apart"
    assert [_NUMBER_WORDS[w.lower()] for w in words] == [len(_DIVERGENCES)]


# ----------------------------------------------- what the guard denies


def _options_before_verbs(text: str) -> set[str]:
    """The backticked options in the clause that ends "on `git diff` and `git log`"."""
    anchor = text.index("on `git diff` and `git log`")
    clause_start = max(text.rfind(";", 0, anchor), text.rfind(" - ", 0, anchor))
    clause = text[clause_start:anchor]
    return {t for t in re.findall(r"`([^`]+)`", clause) if t.startswith("-")}


def test_the_page_names_every_git_option_the_guard_refuses(guard, doc: str):
    expected = set(guard._GIT_DENIED) | {f"-{guard._GIT_DENIED_SHORT}"}
    assert _options_before_verbs(_doc_guard_paragraph(doc)) == expected


def test_the_readme_names_every_git_option_the_guard_refuses(guard, readme: str):
    expected = set(guard._GIT_DENIED) | {f"-{guard._GIT_DENIED_SHORT}"}
    assert _options_before_verbs(_readme_guard_section(readme)) == expected


@pytest.mark.parametrize("option", ["--no-index", "--output=x", "--output-file=x", "--orderfile=x", "-Ox"])
def test_each_named_option_is_refused_on_both_verbs(guard, option: str):
    for verb in ("git diff", "git log"):
        assert guard.decide(f"{verb} {option} HEAD", cwd=_ROOT), f"{verb} {option}"


def test_both_pages_say_an_outside_operand_is_refused(guard, doc: str, readme: str):
    """The spelling that reads `.env` has no option on the line to match."""
    for name, text in (("docs/SECURITY-AI.md", _doc_guard_paragraph(doc)), (".claude/README.md", _readme_guard_section(readme))):
        assert re.search(r"`git diff` operand naming a path outside the checkout", text), name
    assert guard.decide("git diff .env /etc/hostname", cwd=_ROOT)
    assert guard.decide("git diff ~/x HEAD", cwd=_ROOT)
    assert guard.decide("git diff HEAD~1 HEAD", cwd=_ROOT) is None


# The assignment spellings each page must name, with a command that exercises it.
_ASSIGNMENT_SPELLINGS = {
    r"`(VAR|NAME)\+=value`": "X+=1 pytest -q",
    r"bare `export (VAR|NAME)`": "export X; X=1 pytest -q",
    r"`set -a`": "set -a; X=1 pytest -q",
}


@pytest.mark.parametrize("pattern", sorted(_ASSIGNMENT_SPELLINGS))
def test_both_pages_name_each_assignment_spelling_the_guard_refuses(guard, doc: str, readme: str, pattern: str):
    for name, text in (("docs/SECURITY-AI.md", _doc_guard_paragraph(doc)), (".claude/README.md", _readme_guard_section(readme))):
        assert re.search(pattern, text), f"{name} does not name {pattern}"
    assert guard.decide(_ASSIGNMENT_SPELLINGS[pattern], cwd=_ROOT)


def test_the_readme_names_the_export_family_the_guard_refuses(guard, readme: str):
    """`export`, `declare -x`, `typeset -x`, `readonly` -- equal both ways."""
    section = _readme_guard_section(readme)
    family = _between(section, "the export family (", ")")
    named = {t.split()[0] for t in re.findall(r"`([^`]+)`", family)}
    assert named == set(guard._EXPORT_BUILTINS)


def _named_wrappers(text: str, start: str, end: str) -> set[str]:
    clause = _between(text, start, end)
    return {t for t in re.findall(r"`([^`]+)`", clause) if " " not in t}


def test_every_wrapper_either_page_names_is_one_the_guard_refuses(guard, doc: str, readme: str):
    named = _named_wrappers(_doc_guard_paragraph(doc), "a wrapper such as", "that the guard cannot see through")
    named |= _named_wrappers(_readme_guard_section(readme), "a wrapper the guard does not model", "standing in front")
    assert named, "neither page names a wrapper any more"
    assert named <= set(guard._WRAPPERS), sorted(named - set(guard._WRAPPERS))
    for wrapper in named:
        assert guard.decide(f"{wrapper} pytest -q", cwd=_ROOT), wrapper


def test_the_readme_safe_variables_are_on_the_guards_safe_list(guard, readme: str):
    section = _readme_guard_section(readme)
    named = set(re.findall(r"`(GOODREADS_[A-Z_]+)`", section))
    assert named and named <= guard.SAFE_ENV, sorted(named - guard.SAFE_ENV)


# ----------------------------------------------- the permission rules


def test_the_three_layer_summary_names_the_allowed_verbs_exactly(guard, doc: str):
    """"allow (routine: `pytest` and the git verbs ...)" against the allow list."""
    text = _squash(doc)
    allow = _between(text, "allow (routine:", ")")
    words = re.findall(r"`([^`]+)`", allow)
    documented = {w if w == "pytest" else f"git {w}" for w in words}
    granted = {m.group(1) for r in _settings()["allow"] if (m := re.fullmatch(r"Bash\((.+) \*\)", r))}
    assert documented == granted == set(guard.GUARDED)


def test_every_rule_the_page_quotes_is_granted_except_the_one_it_proposes(doc: str):
    """`Bash(pytest)` is the page's untaken alternative; every other rule is live."""
    live = {r for layer in _settings().values() for r in layer}
    quoted = {t for t in _backticked(doc) if re.fullmatch(r"(Bash|Read|Edit|Write)\(.+\)", t)}
    assert quoted - live == {"Bash(pytest)"}
    assert "Bash(pytest *)" in _settings()["allow"]


def test_the_rules_the_page_files_under_deny_are_deny_rules(doc: str):
    """ "the `Read(./.env)` deny entry", "the deny list already uses (...)"."""
    deny = set(_settings()["deny"])
    text = _squash(doc)
    filed = set(re.findall(r"`((?:Bash|Read)\([^`]+\))` deny entry", text))
    already = re.search(r"the deny list already uses \(((?:[^()`]|`[^`]*`)*)\)", text)
    assert already, "the page no longer cites the deny list's exact-plus-prefix pair"
    filed |= set(re.findall(r"`((?:Bash|Read)\([^`]+\))`", already.group(1)))
    assert filed == {"Read(./.env)", "Bash(git reset --hard)", "Bash(git reset --hard *)"}, filed
    assert filed <= deny, sorted(filed - deny)


def test_the_boundary_files_are_tier_2(doc: str):
    """The page defers to "risk tiers, Tier 2" for settings.json and the hooks."""
    assert "[risk tiers](risk-tiers.md), Tier 2" in _squash(doc)
    tier2 = _squash(_between(_RISK_TIERS.read_text(encoding="utf-8"), "## Tier 2", "## Tier 3"))
    covers = set(re.findall(r"`([^`]+)`", _between(tier2, "Covers:", "**Required:**")))
    boundary = _between(_squash(doc), "**Never widen your own boundary.**", "## Treat fetched")
    named = {t for t in re.findall(r"`([^`]+)`", boundary) if t.startswith(".claude/")}
    assert named == {".claude/settings.json", ".claude/hooks/**"}
    assert named <= covers, sorted(named - covers)


# ----------------------------------------------- the rest of the page


def test_the_labeler_is_the_pull_request_target_job_that_runs_nothing(doc: str):
    """"the labeler qualifies": it never checks out or executes PR code."""
    assert "the labeler qualifies" in _squash(doc)
    text = _LABELER.read_text(encoding="utf-8")
    assert re.search(r"^  pull_request_target:", text, re.M)
    assert not re.search(r"^\s*(-\s+)?run:", text, re.M), "the labeler runs a shell step"
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses and not any(u.startswith("actions/checkout") for u in uses), uses


def test_the_client_names_the_page_cites_exist():
    from goodreads_mcp import client

    assert isinstance(client.MAX_IN_FLIGHT, int) and client.MAX_IN_FLIGHT >= 1
    assert callable(client.GoodreadsClient.graphql_config)
    assert issubclass(client.WAFChallenge, Exception)


def test_the_coverage_database_the_page_says_is_gitignored_is(doc: str):
    assert "`.coverage` was committed once here" in _squash(doc)
    probe = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", ".coverage"], cwd=_ROOT, check=False
    )
    assert probe.returncode == 0, ".coverage is not ignored"


def test_every_repository_path_the_page_backticks_is_committed(doc: str):
    paths = [
        t
        for t in _backticked(doc)
        if re.match(r"(\.claude|\.github|tests|docs)/", t) and not t.endswith("/**")
    ]
    assert paths, "the page names no repository path"
    for path in paths:
        assert (_ROOT / path).exists(), path
    assert (_ROOT / ".github" / "workflows" / "ai-fix.yml").is_file()
