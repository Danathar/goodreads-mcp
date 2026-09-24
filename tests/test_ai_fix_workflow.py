"""`ai-fix.yml` is the only workflow here that can write to the repo on a
stranger's say-so, and nothing opened it.

It is triggered by `issues: [labeled]` and `issue_comment: [created]` — two
events any GitHub user with an account can cause — and it runs with
`contents: write`, `issues: write` and `pull-requests: write`. Two things stand
between that grant and an outside caller: a job-level `if:` that decides which
events are a request at all, and a first step that asks the API whether the
actor is a collaborator with write access. `docs/SECURITY-AI.md` cites this
file by name as how the rule is kept:

    Gate any workflow that can write to the repo on the actor's collaborator
    permission, as `ai-fix.yml` does.

Until this file, that citation was the only thing asserting it. No test read
`ai-fix.yml`; `tests/test_nightly_compliance_workflow.py` says so in its own
opening paragraph. And because the coverage gate measures `goodreads_mcp` only,
the gate step could have been deleted outright without moving the number.

So these tests **run the steps**, with the reader and runner in
`tests/_workflow_steps.py` that the release and nightly workflow tests already
share: the body is sliced out by indentation, `${{ }}` is substituted from a
table that raises on anything unknown, and the result is handed to `bash
--noprofile --norc -e` — the shell GitHub actually hands a `run:` body when no
step, job or workflow sets `shell:`, which is true of every workflow here.

Four things were asserted by nothing and are asserted here:

- **The `if:` gate.** It is an expression, so it is *evaluated* — against a
  hand-rolled evaluator for the subset of GitHub's expression language the
  condition uses, which carries its own case table so the payload assertions
  below are not vacuous. A comment that merely mentions `/ai-fix` must not
  fire, and every event in `on:` must be named in the condition, so a trigger
  cannot be widened without widening the gate too. An event a bot sends must
  not fire either: the dashboard app labels every ACMM issue it opens with
  `ai-fix-requested`, and before #143 each of those runs failed the
  collaborator check and left a red run behind.
- **The collaborator check.** Run for each value GitHub's
  `collaborators/*/permission` API returns, plus the shapes that are not a
  value at all: a failed call, and an empty answer. Only `admin` and `write`
  may proceed, and the `--jq` path is joined to that set — `.role_name` would
  return `maintain` for a collaborator this case list then refuses.
- **The acknowledgement.** It is the run's whole output, and every claim in it
  is a hand copy of something else in the repo: two files it tells the agent to
  read, the "read-only and unauthenticated" scope from `docs/SECURITY-AI.md`,
  and the live-suite command whose env var is what actually un-skips
  `tests/e2e`. Each is joined back to its source.
- **"No agent is wired up yet."** The last step says that in the run summary.
  It is true only while no workflow names `anthropics/claude-code-action` in a
  `uses:`; the moment one does, the summary is a lie and this test says so.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_AI_FIX = _ROOT / ".github" / "workflows" / "ai-fix.yml"
_WORKFLOW_DIR = _ROOT / ".github" / "workflows"
_SECURITY = _ROOT / "docs" / "SECURITY-AI.md"
_AGENTS = _ROOT / "AGENTS.md"
_RUBRIC = _ROOT / "docs" / "review-rubric.md"
_LIVE_SUITE = _ROOT / "tests" / "e2e" / "test_smoke_live.py"

# The workflow's own step names.
_GATE_STEP = "Check requester is a maintainer"
_ACK_STEP = "Acknowledge the request"
_SUMMARY_STEP = "Agent step (not configured)"

_EXECUTED = {_GATE_STEP, _ACK_STEP, _SUMMARY_STEP}

_WORKFLOW = _workflow_steps.Workflow(_AI_FIX)
_TEXT = _WORKFLOW.text
_STEPS = _WORKFLOW.steps
_step = _WORKFLOW.step
_write_stub = _workflow_steps.write_stub
_recorder = _workflow_steps.recorder
_argv = _workflow_steps.argv
_run = _workflow_steps.run


def _body(name: str, values: dict[str, str] | None = None) -> str:
    return _WORKFLOW.body(name, values)


# --------------------------------------------------------------------------
# A very small evaluator for the subset of GitHub's expression language the
# job-level `if:` uses: string literals, context lookups, `==`/`!=`, `&&`/`||`,
# parentheses, and `startsWith`/`contains`.
#
# Loose equality follows the documented rule — operands of different types are
# cast to a number, so `null` (0) equals `''` (0) but not `'x'` (NaN). The case
# table in `test_the_expression_evaluator_agrees_with_github` is what keeps the
# payload assertions below from being vacuous: an evaluator that answered
# `True` to everything would pass them all.
# --------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<str>'[^']*')|(?P<op>==|!=|&&|\|\||[(),])|(?P<name>[A-Za-z_][A-Za-z0-9_.]*))"
)


def _tokenize(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expression):
        if expression[pos].isspace():
            pos += 1
            continue
        match = _TOKEN.match(expression, pos)
        if not match:
            raise AssertionError(f"cannot tokenise {expression[pos:]!r}")
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
        pos = match.end()
    return tokens


class _Expression:
    """Parse once, evaluate against many contexts."""

    def __init__(self, source: str):
        self.source = source
        self.tokens = _tokenize(source)

    def __call__(self, context: dict) -> bool:
        self._pos = 0
        self._context = context
        value = self._or()
        assert self._pos == len(self.tokens), f"trailing tokens in {self.source!r}"
        return _truth(value)

    # -- recursive descent --------------------------------------------------

    def _peek(self) -> str | None:
        return self.tokens[self._pos][1] if self._pos < len(self.tokens) else None

    def _take(self, expected: str) -> None:
        assert self._peek() == expected, f"expected {expected!r} in {self.source!r}"
        self._pos += 1

    def _or(self):
        value = self._and()
        while self._peek() == "||":
            self._pos += 1
            right = self._and()
            value = value if _truth(value) else right
        return value

    def _and(self):
        value = self._comparison()
        while self._peek() == "&&":
            self._pos += 1
            right = self._comparison()
            value = right if _truth(value) else value
        return value

    def _comparison(self):
        left = self._primary()
        operator = self._peek()
        if operator in ("==", "!="):
            self._pos += 1
            right = self._primary()
            equal = _loose_equal(left, right)
            return equal if operator == "==" else not equal
        return left

    def _primary(self):
        kind, text = self.tokens[self._pos]
        if text == "(":
            self._pos += 1
            value = self._or()
            self._take(")")
            return value
        self._pos += 1
        if kind == "str":
            return text[1:-1]
        if kind != "name":
            raise AssertionError(f"unexpected {text!r} in {self.source!r}")
        if self._peek() == "(":
            self._pos += 1
            args = [self._or()]
            while self._peek() == ",":
                self._pos += 1
                args.append(self._or())
            self._take(")")
            return _call(text, args)
        if text == "true":
            return True
        if text == "false":
            return False
        if text == "null":
            return None
        return _lookup(self._context, text)


def _lookup(context: dict, path: str):
    value = context
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _call(name: str, args: list):
    text = [_as_string(arg) for arg in args]
    if name == "startsWith":
        return text[0].startswith(text[1])
    if name == "contains":
        return text[1] in text[0]
    raise AssertionError(f"this evaluator does not implement {name}()")


def _as_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _truth(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return value != ""


def _loose_equal(left, right) -> bool:
    """`null` casts to 0, `''` casts to 0, any other string to NaN."""
    if left is None and isinstance(right, str):
        return right == ""
    if right is None and isinstance(left, str):
        return left == ""
    return left == right


def test_the_expression_evaluator_agrees_with_github():
    """Without this table the payload assertions below would prove nothing."""
    context = {"github": {"event_name": "issues", "event": {"label": {"name": "bug"}}}}
    cases = [
        ("'a' == 'a'", True),
        ("'a' == 'b'", False),
        ("'a' != 'b'", True),
        ("github.event_name == 'issues'", True),
        ("github.event_name == 'issue_comment'", False),
        ("github.event.label.name == 'bug'", True),
        # A path that is not in the payload at all is null, not an error.
        ("github.event.comment.body == 'hi'", False),
        ("github.event.comment.body == ''", True),
        ("startsWith(github.event.comment.body, '/x')", False),
        ("startsWith('/ai-fix now', '/ai-fix')", True),
        ("startsWith('please /ai-fix', '/ai-fix')", False),
        ("contains('please /ai-fix', '/ai-fix')", True),
        # `&&` binds tighter than `||`, so this is (F && F) || T.
        ("'a' == 'b' && 'a' == 'b' || 'a' == 'a'", True),
        ("'a' == 'a' || 'a' == 'b' && 'b' == 'c'", True),
        ("('a' == 'a' || 'a' == 'b') && 'b' == 'c'", False),
        ("true && false", False),
        ("false || true", True),
    ]
    for source, expected in cases:
        assert _Expression(source)(context) is expected, source


# --------------------------------------------------------------------------
# `on:` and the job-level `if:`
# --------------------------------------------------------------------------


def _triggers() -> dict[str, list[str]]:
    """The `on:` block, as event name -> the `types:` it is narrowed to."""
    block = re.search(r"^on:\n((?:[ \t].*\n|\n)+)", _TEXT, re.M)
    assert block, "ai-fix.yml has no on: block"
    events: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.group(1).splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 2:
            current = line.strip().rstrip(":")
            events[current] = []
        elif indent == 4 and current is not None:
            types = re.match(r"types:\s*\[(.*)\]$", line.strip())
            if types:
                events[current] = [item.strip() for item in types.group(1).split(",")]
    return events


def _job_condition() -> str:
    """The `requested:` job's `if:`, folded back to one line."""
    match = re.search(r"^    if: >-\n((?:      .*\n)+)", _TEXT, re.M)
    assert match, "the requested: job no longer carries a folded if:"
    return " ".join(line.strip() for line in match.group(1).splitlines() if line.strip())


_CONDITION = _job_condition()
_GATE = _Expression(_CONDITION)


def _event(name: str, **payload) -> dict:
    return {"github": {"event_name": name, "event": payload}}


def _labeled(label: str, sender: str = "User") -> dict:
    return _event("issues", label={"name": label}, issue={"number": 7}, sender={"type": sender})


def _commented(body: str, sender: str = "User") -> dict:
    return _event(
        "issue_comment", comment={"body": body}, issue={"number": 7}, sender={"type": sender}
    )


@pytest.mark.parametrize(
    "context,fires,why",
    [
        (_labeled("ai-fix-requested"), True, "the label the header documents"),
        (_labeled("bug"), False, "any other label must not start a write-capable run"),
        (_labeled(""), False, "an unnamed label is not the request label"),
        (_commented("/ai-fix"), True, "the bare command"),
        (_commented("/ai-fix please rerun the parser"), True, "the command with an argument"),
        (_commented("please /ai-fix"), False, "a mention mid-sentence is not a request"),
        (_commented("ai-fix"), False, "no leading slash"),
        (_commented("/ai-fixture"), True, "startsWith is a prefix test, by design"),
        (_commented(""), False, "an empty comment"),
        # A bot sender is skipped, on either path, before the collaborator
        # check can refuse it and turn the run red (#143).
        (_labeled("ai-fix-requested", sender="Bot"), False,
         "the dashboard app applying the label is not a request"),
        (_commented("/ai-fix", sender="Bot"), False, "nor a bot writing the command"),
        # The event guards: neither branch may fire on the other's payload.
        (_event("issues", comment={"body": "/ai-fix"}, issue={"number": 7}), False,
         "a comment body on an issues event is not a request"),
        (_event("issue_comment", label={"name": "ai-fix-requested"}, issue={"number": 7}), False,
         "a label name on an issue_comment event is not a request"),
        (_event("pull_request_target", label={"name": "ai-fix-requested"}), False,
         "no event outside on: may reach the job"),
        (_event("workflow_dispatch"), False, "nor a manual dispatch"),
    ],
)
def test_the_job_condition_fires_only_on_an_explicit_request(context, fires, why):
    assert _GATE(context) is fires, why


def test_every_trigger_in_on_is_named_in_the_condition():
    """Widening `on:` without widening the gate would let an ungated event run."""
    triggered = set(_triggers())
    guarded = set(re.findall(r"github\.event_name\s*==\s*'([^']+)'", _CONDITION))
    assert triggered == guarded, (
        "ai-fix.yml's triggers and the events its if: names have diverged; "
        f"ungated: {sorted(triggered - guarded)}, stale: {sorted(guarded - triggered)}"
    )


def test_the_triggers_are_narrowed_to_the_two_events_that_are_requests():
    """`issues` unnarrowed fires on `opened`, which nobody asked for."""
    assert _triggers() == {"issues": ["labeled"], "issue_comment": ["created"]}


def test_the_workflow_is_not_triggered_by_pull_request_target():
    """Its own header calls that shape the classic privilege escalation.

    The words appear in the file — in the comment saying why they are absent —
    so the check is that every occurrence is a comment and none is a trigger.
    """
    assert "pull_request_target" not in _triggers()
    mentions = [line for line in _TEXT.splitlines() if "pull_request_target" in line]
    assert mentions, "the header no longer explains why this trigger is absent"
    assert all(line.lstrip().startswith("#") for line in mentions), mentions
    assert "pull_request_target" in _SECURITY.read_text(encoding="utf-8"), (
        "docs/SECURITY-AI.md no longer states the rule this test enforces"
    )


def test_requests_queue_rather_than_cancelling_each_other():
    """`cancel-in-progress: true` would abort a fix in flight when a second is asked for."""
    assert re.search(
        r"^concurrency:\n  group: ai-fix-\$\{\{ github\.event\.issue\.number \}\}\n"
        r"  cancel-in-progress: false$",
        _TEXT,
        re.M,
    )


def test_the_grant_is_the_three_write_scopes_the_job_uses_and_no_more():
    permissions = re.search(r"^permissions:\n((?:  .*\n)+)", _TEXT, re.M)
    assert permissions
    granted = dict(
        line.strip().split(": ", 1) for line in permissions.group(1).splitlines() if line.strip()
    )
    assert granted == {"contents": "write", "issues": "write", "pull-requests": "write"}


def test_a_write_capable_workflow_on_an_outsider_event_either_gates_or_runs_nothing():
    """The rule `docs/SECURITY-AI.md` states, enforced over every workflow.

    An outsider can cause `issues`, `issue_comment` and `pull_request_target`.
    A workflow they can trigger that holds a `write` scope must either gate on
    the actor's collaborator permission, or execute nothing at all — which is
    what makes the labeler's `pull_request_target` safe.
    """
    outsider = ("issues", "issue_comment", "pull_request_target")
    checked = 0
    for path in sorted(_WORKFLOW_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        triggers = re.search(r"^on:\n((?:[ \t].*\n|\n)+)", text, re.M)
        assert triggers, f"{path.name} has no on: block"
        events = re.findall(r"^  ([a-z_]+):", triggers.group(1), re.M)
        if not set(events) & set(outsider):
            continue
        permissions = re.search(r"^permissions:\n((?:  .*\n)+)", text, re.M)
        assert permissions, (
            f"{path.name} is triggerable by an outsider and declares no permissions:, "
            "so it runs with whatever the repository default grants"
        )
        if "write" not in permissions.group(1):
            continue
        checked += 1
        gated = "collaborators/" in text and "/permission" in text
        runs_code = "run:" in text or "actions/checkout" in text
        assert gated or not runs_code, (
            f"{path.name} runs on an outsider-triggerable event with a write scope, "
            "but neither gates on collaborator permission nor stays code-free"
        )
    assert checked >= 2, "expected ai-fix.yml and labeler.yml to reach this rule"


# --------------------------------------------------------------------------
# Step 1 — the collaborator gate
# --------------------------------------------------------------------------


@pytest.fixture()
def gh_stub(tmp_path: Path):
    """A recording `gh` whose answer and exit status the test picks."""
    directory = tmp_path / "bin"
    directory.mkdir()
    log = tmp_path / "gh.log"
    _write_stub(
        directory,
        "gh",
        _recorder(log)
        + 'if [ -n "${STUB_STDERR:-}" ]; then printf "%s\\n" "$STUB_STDERR" >&2; fi\n'
        + 'if [ -n "${STUB_ROLE+set}" ]; then printf "%s\\n" "$STUB_ROLE"; fi\n'
        + 'exit "${STUB_RC:-0}"\n',
    )
    return directory, log


def _run_gate(tmp_path: Path, gh_stub, **stub_env):
    directory, log = gh_stub
    env = {
        "GITHUB_REPOSITORY": "Danathar/goodreads-mcp",
        "GH_TOKEN": "t0ken",
        "ACTOR": "someone",
        **stub_env,
    }
    result = _run(_body(_GATE_STEP), tmp_path, path_dirs=[directory], env=env)
    return result, _argv(log)


@pytest.mark.parametrize("role", ["admin", "write"])
def test_a_collaborator_with_write_access_proceeds(tmp_path, gh_stub, role):
    result, _ = _run_gate(tmp_path, gh_stub, STUB_ROLE=role)
    assert result.returncode == 0, result.stderr
    assert f"someone has {role} — proceeding" in result.stdout
    assert "::error::" not in result.stdout


@pytest.mark.parametrize(
    "role,shown",
    [
        ("read", "read"),
        ("none", "none"),
        # Not values of `.permission`, but a case list is only as good as its
        # default arm: anything that is not admin or write must be refused.
        ("triage", "triage"),
        ("maintain", "maintain"),
        ("Write", "Write"),
        ("", ""),
    ],
)
def test_anyone_else_is_refused(tmp_path, gh_stub, role, shown):
    result, _ = _run_gate(tmp_path, gh_stub, STUB_ROLE=role)
    assert result.returncode == 1
    assert f"::error::someone lacks write access (got '{shown}'); refusing." in result.stdout


def test_a_failed_api_call_is_refused_rather_than_treated_as_absent(tmp_path, gh_stub):
    """`|| echo none` is what turns a 404, a rate limit or a dead token into a refusal."""
    result, _ = _run_gate(tmp_path, gh_stub, STUB_RC="1")
    assert result.returncode == 1
    assert "lacks write access (got 'none')" in result.stdout


def test_a_silent_api_call_is_refused(tmp_path, gh_stub):
    """`gh` exiting 0 with nothing on stdout must not read as permission."""
    result, _ = _run_gate(tmp_path, gh_stub, STUB_RC="0")
    assert result.returncode == 1
    assert "lacks write access (got '')" in result.stdout


def test_the_api_noise_is_suppressed_but_the_refusal_is_not(tmp_path, gh_stub):
    """`2>/dev/null` keeps a raw HTTP error out of the log; the `::error::` stays."""
    result, _ = _run_gate(
        tmp_path, gh_stub, STUB_RC="1", STUB_STDERR="gh: HTTP 404 (rate limited)"
    )
    assert "HTTP 404" not in result.stderr and "HTTP 404" not in result.stdout
    assert "::error::" in result.stdout


def test_the_gate_asks_the_api_about_this_repo_and_this_actor(tmp_path, gh_stub):
    result, calls = _run_gate(
        tmp_path, gh_stub, STUB_ROLE="admin", GITHUB_REPOSITORY="o/r", ACTOR="octocat"
    )
    assert result.returncode == 0
    assert len(calls) == 1, calls
    assert calls[0][1:] == [
        "api",
        "repos/o/r/collaborators/octocat/permission",
        "--jq",
        ".permission",
    ]


def test_the_gate_reads_permission_and_not_role_name():
    """`.role_name` answers `maintain` for a collaborator this case list refuses."""
    body = _body(_GATE_STEP)
    assert "'.permission'" in body
    assert "role_name" not in body
    accepted = re.search(r"^\s*([a-z|]+)\)\s*echo", body, re.M)
    assert accepted and accepted.group(1) == "admin|write"


def test_the_gate_carries_a_token_and_the_actor_from_the_event():
    step = _step(_GATE_STEP)
    assert step.env["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert step.env["ACTOR"] == "${{ github.actor }}"


def test_the_gate_runs_before_anything_is_checked_out_or_commented():
    """A gate that runs after the work is not a gate."""
    order = [step.name or step.uses for step in _STEPS]
    checkout = [i for i, name in enumerate(order) if name.startswith("actions/checkout@")]
    assert len(checkout) == 1, order
    assert order.index(_GATE_STEP) == 0
    assert order.index(_GATE_STEP) < checkout[0] < order.index(_ACK_STEP)


# --------------------------------------------------------------------------
# Step 2 — the acknowledgement
# --------------------------------------------------------------------------

_ACK_ENV = {
    "GH_TOKEN": "t0ken",
    "NUM": "42",
    "GITHUB_ACTOR": "octocat",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "Danathar/goodreads-mcp",
    "GITHUB_RUN_ID": "987654",
}


@pytest.fixture()
def ack(tmp_path: Path):
    directory = tmp_path / "bin"
    directory.mkdir()
    log = tmp_path / "gh.log"
    _write_stub(directory, "gh", _recorder(log))
    result = _run(_body(_ACK_STEP), tmp_path, path_dirs=[directory], env=_ACK_ENV)
    assert result.returncode == 0, result.stderr
    calls = _argv(log)
    assert len(calls) == 1, calls
    return calls[0]


def test_the_acknowledgement_comments_on_the_issue_that_asked(ack):
    assert ack[1:4] == ["issue", "comment", "42"]
    assert ack[4] == "--body"
    assert len(ack) == 6


def test_the_acknowledgement_links_back_to_the_run_that_posted_it(ack):
    """Four `%s` and four arguments — a miscount silently shifts every field."""
    assert "AI fix requested by @octocat" in ack[5]
    assert "https://github.com/Danathar/goodreads-mcp/actions/runs/987654" in ack[5]


def test_the_acknowledgement_format_string_takes_exactly_its_arguments():
    body = _body(_ACK_STEP)
    fmt = re.search(r"printf '(.*?)' \\\n", body, re.S)
    assert fmt, "the acknowledgement no longer builds its body with one printf"
    assert re.findall(r"%.", fmt.group(1)) == ["%s"] * 4
    arguments = re.search(r'"\$GITHUB_ACTOR" (.*?)\)"', body, re.S)
    assert arguments
    assert arguments.group(1).split() == [
        '"$GITHUB_SERVER_URL"',
        '"$GITHUB_REPOSITORY"',
        '"$GITHUB_RUN_ID"',
    ]


def test_the_acknowledgement_names_files_that_exist(ack):
    """It tells the agent to read two documents before touching anything."""
    for name in ("AGENTS.md", "docs/review-rubric.md"):
        assert f"`{name}`" in ack[5]
        assert (_ROOT / name).is_file(), f"the acknowledgement points at a missing {name}"


def test_the_acknowledgement_states_the_scope_the_security_doc_states(ack):
    """"Adding auth is a fix" is the single most likely wrong finding to act on."""
    assert "**read-only and unauthenticated**" in ack[5]
    assert "**read-only, unauthenticated**" in _SECURITY.read_text(encoding="utf-8")
    assert "No auth, no cookies, no writes" in _RUBRIC.read_text(encoding="utf-8")


def test_the_acknowledgement_names_the_command_that_actually_unskips_the_live_suite(ack):
    """A renamed env var would leave this instruction a no-op that still reads right."""
    assert "GOODREADS_LIVE=1 pytest tests/e2e -v" in ack[5]
    live = _LIVE_SUITE.read_text(encoding="utf-8")
    assert 'os.environ.get("GOODREADS_LIVE") != "1"' in live
    assert (_ROOT / "tests" / "e2e").is_dir()
    assert "GOODREADS_LIVE=1 pytest tests/e2e -v" in _AGENTS.read_text(encoding="utf-8")


def test_the_acknowledgement_says_a_green_offline_suite_proves_nothing(ack):
    """The same sentence `docs/risk-tiers.md` puts on its highest tier."""
    assert "A green offline suite proves nothing about parsing" in ack[5]
    tiers = (_ROOT / "docs" / "risk-tiers.md").read_text(encoding="utf-8")
    assert "a green offline\n  suite is not evidence here" in tiers


def test_the_acknowledgement_is_posted_even_though_no_agent_runs(ack):
    """Silence on a requested run reads as a broken workflow, not a gated one."""
    assert "AI fix requested" in ack[5]


# --------------------------------------------------------------------------
# Step 3 — the run summary
# --------------------------------------------------------------------------


@pytest.fixture()
def summary(tmp_path: Path) -> str:
    written = tmp_path / "summary.md"
    written.write_text("earlier content\n", encoding="utf-8")
    result = _run(
        _body(_SUMMARY_STEP),
        tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(written)},
    )
    assert result.returncode == 0, result.stderr
    text = written.read_text(encoding="utf-8")
    assert text.startswith("earlier content\n"), "the summary is appended to, not overwritten"
    return text


def test_the_summary_says_the_gate_ran_and_nothing_else_did(summary):
    assert "## AI fix requested" in summary
    assert "Gating and acknowledgement ran. No agent is wired up yet." in summary


def test_the_summary_says_what_enabling_the_agent_takes(summary):
    """The backticks are escaped inside a double-quoted `echo`; they must survive."""
    assert "add an `ANTHROPIC_API_KEY` secret" in summary
    assert "`anthropics/claude-code-action`" in summary


def test_the_summary_is_telling_the_truth_about_no_agent_being_wired_up():
    """The moment a workflow `uses:` the action, this step's text is false."""
    for path in sorted(_WORKFLOW_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(-\s*)?uses:\s*anthropics/", text, re.M), (
            f"{path.name} wires up an agent; ai-fix.yml's summary still says none is"
        )
        assert "secrets.ANTHROPIC_API_KEY" not in text, (
            f"{path.name} reads an agent key; ai-fix.yml's summary still says none exists"
        )


def test_the_summary_step_is_the_only_one_that_needs_no_token():
    """It writes a file; a `GH_TOKEN` here would be a grant with no user."""
    assert _step(_SUMMARY_STEP).env == {}


# --------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------


def test_the_checkout_fetches_the_whole_history():
    """A shallow clone cannot be the base for a fix that needs to look backwards."""
    checkout = [step for step in _STEPS if step.uses.startswith("actions/checkout@")]
    assert len(checkout) == 1
    assert checkout[0].with_.get("fetch-depth") == "0"


def test_every_run_step_in_the_ai_fix_workflow_is_executed_by_this_file():
    """A new step must be run here, or listed here as deliberately not run."""
    present = _WORKFLOW.run_step_names()
    assert present == _EXECUTED, (
        "ai-fix.yml's run: steps and the set this file executes have diverged; "
        f"unrun: {sorted(present - _EXECUTED)}, stale: {sorted(_EXECUTED - present)}"
    )
