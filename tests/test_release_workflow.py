"""`release.yml` is the only thing that publishes the bundle; nothing ran it.

The coverage gate measures `goodreads_mcp` and nothing else, so every line of
shell in `.github/workflows/**` sits outside it — a release step could be
deleted without moving the number a point. Until this file, no test opened
`release.yml` at all: the two workflows any test reads are `ci.yml` and
`labeler.yml`, and that read is a regex for `--cov-fail-under`, which is
reading a constant out of a file, not executing the step around it.

So these tests **run the steps**. Each `run:` body is extracted from the
workflow by indentation (the test extra is `pytest` + `pytest-cov`; there is no
PyYAML here and CI installs none), the `${{ }}` expressions GitHub would have
substituted are substituted with an explicit table that raises on anything
unknown, and the body is handed to `bash --noprofile --norc -e -o pipefail`
exactly as the runner hands it over — in a temp directory, against fixture
files and real throwaway git repositories, with recording stubs on `PATH` for
the tools that would reach the network.

Since #165 the workflow has jobs that each run on their own: `prepare` opens
the CalVer version-bump pull request; `build-pypi` builds the wheel and sdist
in a checkout nothing else has run in (#175); and `release` tags and publishes
the version the merged `pyproject.toml` carries — monthly, or by hand. Inputs
reach every script through `env:`, so no `run:` body holds a `${{ }}` at all,
and the tests pass inputs the same way.

The contracts here that are load-bearing:

- **The version-sync gate.** `docs/review-rubric.md` tells a reviewer that
  "`pyproject.toml` and `manifest.json` versions in sync — release CI fails",
  and `docs/risk-tiers.md` makes the paired bump a Tier 2 rule. That promise is
  five lines of shell in one step. It is exercised here both ways, and the
  committed tree is put through it.
- **The release gates.** A scheduled run is opt-in; a month with no change
  under `goodreads_mcp/` is skipped; red checks refuse; a version that is not
  CalVer, is already tagged, is not newer than the last stable release, or
  whose `b1`/`rc1` suffix disagrees with the `prerelease` box is refused before
  anything is written. Every `steps.<id>.outputs.<name>` reference in the file
  is joined back to the body that writes it, because a typo there does not fail
  anything — it quietly skips the release.
- **The bundle is portable.** The v0.1.1 bundle vendored mcp's dependency
  closure with `pip install --target` on one runner, and four of those packages
  are compiled: the `.mcpb` declared three platforms and started on one (#89).
  Nothing ships pre-resolved now -- `manifest.json` launches through `uv run`,
  which resolves `pyproject.toml` on the user's machine -- and two steps hold
  that line: one fails the release if a compiled module is inside the bundle
  while the manifest lists more than one platform, the other unpacks the bundle
  and starts it with the manifest's own command, so a file `.mcpbignore` drops
  is caught before it ships.

A `run:` step that is not in `_EXECUTED` or `_PREPARE_EXECUTED` below fails the
last tests in the file, so a new step cannot be added without either running it
or saying out loud that it is not run.

The reader and the runner themselves live in `tests/_workflow_steps.py`, which
this file and `tests/test_nightly_compliance_workflow.py` share.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_RELEASE_TEXT = _RELEASE.read_text(encoding="utf-8")
_PYPROJECT = _ROOT / "pyproject.toml"
_MANIFEST = _ROOT / "manifest.json"
_RUBRIC = _ROOT / "docs" / "review-rubric.md"

# Every step in the release job that carries a `run:`. Names are the workflow's own.
_ENABLED_STEP = "Check the schedule is enabled"
_DECIDE_STEP = "Decide whether to release"
_CHECKS_STEP = "Require green checks on this commit"
_PIP_STEP = "Upgrade pip"
_VERSION_STEP = "Read the version from pyproject.toml"
_SYNC_STEP = "Check manifest.json matches pyproject.toml"
_VALIDATE_STEP = "Validate the version"
_TEST_STEP = "Install test deps and run tests"
_PACK_STEP = "Pack MCPB"
_COMPILED_STEP = "Check the bundle carries no compiled code"
_LAUNCH_STEP = "Start the packed bundle the way the manifest launches it"
_PYPI_BUILD_STEP = "Build the PyPI distributions"
_PYPI_UPLOAD_STEP = "Upload the PyPI distributions"
_TAG_STEP = "Tag the approved commit"
_PUBLISH_STEP = "Create GitHub Release and upload .mcpb"
_SUMMARY_STEP = "Summarise"
_CLEANUP_STEP = "Clean up the bundle (avoid committing)"

_EXECUTED = {
    _ENABLED_STEP,
    _DECIDE_STEP,
    _CHECKS_STEP,
    _PIP_STEP,
    _VERSION_STEP,
    _SYNC_STEP,
    _VALIDATE_STEP,
    _TEST_STEP,
    _PACK_STEP,
    _COMPILED_STEP,
    _LAUNCH_STEP,
    _TAG_STEP,
    _SUMMARY_STEP,
    _CLEANUP_STEP,
}

# The build job's one `run:` step.
_BUILD_EXECUTED = {_PYPI_BUILD_STEP}

# The packer, pinned: `npx -y <this> pack`. ci.yml validates with the same one.
_PACKER_MATCH = re.search(r"npx -y (@anthropic-ai/mcpb\S*) pack", _RELEASE_TEXT)
assert _PACKER_MATCH, "release.yml no longer packs with npx -y @anthropic-ai/mcpb"
_PACKER = _PACKER_MATCH.group(1)

# The prepare job's `run:` steps.
_NEXT_STEP = "Decide the next version"
_BUMP_STEP = "Bump pyproject.toml and manifest.json"
_PR_STEP = "Open the pull request"

_PREPARE_EXECUTED = {_NEXT_STEP, _BUMP_STEP, _PR_STEP}

# The condition every release step after the decision shares, verbatim.
_GO_IF = "steps.decide.outputs.go == 'true'"
_WRITE_IF = f"{_GO_IF} && !inputs.dry_run"

# The one CalVer regex, as #165 specifies it, and as the workflow defines it.
_CALVER_SPEC = r"^[0-9]{4}\.(1[0-2]|[1-9])\.(0|[1-9][0-9]*)((b|rc)[1-9][0-9]*)?$"
_CALVER_MATCH = re.search(r"^  CALVER_PATTERN: '(.+)'$", _RELEASE_TEXT, re.M)
assert _CALVER_MATCH, "release.yml no longer defines CALVER_PATTERN in its top-level env:"
_CALVER = _CALVER_MATCH.group(1)

_OUTPUT_REF = _workflow_steps.OUTPUT_REF

_WORKFLOW = _workflow_steps.Workflow(_RELEASE, job="release")
_STEPS = _WORKFLOW.steps
_step = _WORKFLOW.step
_PREPARE = _workflow_steps.Workflow(_RELEASE, job="prepare")
_PUBLISH_PYPI = _workflow_steps.Workflow(_RELEASE, job="publish-pypi")
_BUILD_PYPI = _workflow_steps.Workflow(_RELEASE, job="build-pypi")
_write_stub = _workflow_steps.write_stub
_recorder = _workflow_steps.recorder
_argv = _workflow_steps.argv
_outputs = _workflow_steps.outputs


def _body(name: str, values: dict[str, str] | None = None) -> str:
    return _WORKFLOW.body(name, values)


@pytest.fixture(scope="module")
def python_shim(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A `python` on PATH, which the runner has via setup-python and we don't.

    It must `exec` this interpreter rather than symlink it: a symlink resolves
    away from the virtualenv the suite is running in. `python3` is the same
    interpreter, for the prepare job, which uses the runner's own.
    """
    directory = tmp_path_factory.mktemp("python-shim")
    _write_stub(directory, "python", f'exec "{sys.executable}" "$@"\n')
    _write_stub(directory, "python3", f'exec "{sys.executable}" "$@"\n')
    return directory


def _run(
    body: str,
    cwd: Path,
    *,
    path_dirs: list[Path] | None = None,
    env: dict[str, str] | None = None,
    github_output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return _workflow_steps.run(
        body,
        cwd,
        path_dirs=path_dirs,
        env={"CALVER_PATTERN": _CALVER, **(env or {})},
        github_output=github_output,
        pipefail=True,
    )


# --------------------------------------------------------------------------
# The CalVer pattern
# --------------------------------------------------------------------------


def test_the_workflow_defines_the_calver_pattern_the_issue_specifies():
    """One regex owns the format (#165); both jobs read it from the top-level env."""
    assert _CALVER == _CALVER_SPEC
    assert _RELEASE_TEXT.count(_CALVER_SPEC) == 1, "the pattern is written out more than once"


@pytest.mark.parametrize(
    ("version", "valid"),
    [
        ("2026.10.0", True),
        ("2026.1.0", True),
        ("2026.12.13", True),
        ("2026.10.0b1", True),
        ("2026.10.0rc12", True),
        ("2026.09.0", False),  # zero-padded month
        ("2026.13.0", False),
        ("2026.0.0", False),
        ("2026.10.01", False),  # zero-padded patch
        ("v2026.10.0", False),
        ("2026.10", False),
        ("26.10.0", False),
        ("2026.10.0b0", False),
        ("2026.10.0a1", False),
        ("2026.10.0.post1", False),
        ("0.1.2", False),
        ("2026.10.0\nx", False),
        ("2026.10.0\n", False),
    ],
)
def test_the_calver_pattern_accepts_exactly_the_documented_shape(tmp_path: Path, version: str, valid: bool):
    """Applied the way every step applies it: bash `=~`, anchored to the whole value."""
    result = _run('[[ "$V" =~ $CALVER_PATTERN ]]\n', tmp_path, env={"V": version})
    assert (result.returncode == 0) is valid


def test_the_pattern_is_never_applied_line_by_line():
    """`grep -E` would pass "2026.10.0<newline>anything" on its first line."""
    bodies = "".join(step.run or "" for workflow in (_WORKFLOW, _PREPARE) for step in workflow.steps)
    assert bodies.count("=~ $CALVER_PATTERN ]]") == 4
    assert not re.search(r"grep[^\n]*CALVER_PATTERN", bodies)


def test_the_committed_version_is_a_valid_python_version():
    """PyPI takes the version unchanged; CalVer written this way is PEP 440."""
    match = re.search(r'^version = "(.+?)"', _PYPROJECT.read_text(encoding="utf-8"), re.M)
    assert match, "pyproject.toml no longer declares a version"
    version = match.group(1)
    assert re.fullmatch(r"[0-9]+(\.[0-9]+)*((b|rc)[0-9]+)?", version), version


# --------------------------------------------------------------------------
# Upgrade pip
# --------------------------------------------------------------------------


def test_the_pip_upgrade_step_runs_pip_from_the_selected_interpreter(tmp_path: Path):
    """`python -m pip`, not bare `pip` — the two can be different installs."""
    log = tmp_path / "argv"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "python", _recorder(log))

    result = _run(_body(_PIP_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0, result.stderr
    assert _argv(log) == [[str(stubs / "python"), "-m", "pip", "install", "--upgrade", "pip"]]


# --------------------------------------------------------------------------
# Read the version from pyproject.toml
# --------------------------------------------------------------------------


def _pyproject(version: str) -> str:
    return f'[project]\nname = "goodreads-mcp"\nversion = "{version}"\n'


def _read_version(
    tmp_path: Path, python_shim: Path, version: str, given: str = ""
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    (tmp_path / "pyproject.toml").write_text(_pyproject(version), encoding="utf-8")
    output = tmp_path / "github_output"
    result = _run(
        _body(_VERSION_STEP),
        tmp_path,
        path_dirs=[python_shim],
        env={"GIVEN_VERSION": given},
        github_output=output,
    )
    return result, _outputs(output)


def test_the_version_step_reads_the_version_off_pyproject(tmp_path: Path, python_shim: Path):
    result, outputs = _read_version(tmp_path, python_shim, "2026.10.0")

    assert result.returncode == 0, result.stderr
    assert outputs == {"version": "2026.10.0"}
    assert "Version: 2026.10.0" in result.stdout


def test_the_version_step_publishes_no_v_prefixed_tag(tmp_path: Path, python_shim: Path):
    """CalVer tags are the bare version; v0.1.1 is the last tag with a `v`."""
    _, outputs = _read_version(tmp_path, python_shim, "2026.10.0")
    assert "tag" not in outputs
    assert "v${{" not in _RELEASE_TEXT and "tag=v" not in _RELEASE_TEXT


def test_an_input_may_confirm_the_version(tmp_path: Path, python_shim: Path):
    result, outputs = _read_version(tmp_path, python_shim, "2026.10.0b1", given="2026.10.0b1")

    assert result.returncode == 0, result.stderr
    assert outputs == {"version": "2026.10.0b1"}


def test_an_input_may_not_choose_the_version(tmp_path: Path, python_shim: Path):
    """The number comes from the merged file; a different input is refused, not obeyed."""
    result, outputs = _read_version(tmp_path, python_shim, "2026.10.0", given="2026.10.1")

    assert result.returncode == 1
    assert "asked for 2026.10.1, but pyproject.toml says 2026.10.0" in result.stderr
    assert outputs == {}


@pytest.mark.parametrize("given", ["2026.09.0", "v2026.10.0", "2026.10.0\nversion=evil", "$(touch pwned)"])
def test_a_malformed_input_is_refused_before_it_is_used(tmp_path: Path, python_shim: Path, given: str):
    result, outputs = _read_version(tmp_path, python_shim, "2026.10.0", given=given)

    assert result.returncode == 1
    assert "is not CalVer" in result.stderr
    assert outputs == {}
    assert not (tmp_path / "pwned").exists()


def test_a_pyproject_that_is_not_calver_is_refused(tmp_path: Path, python_shim: Path):
    """0.1.x is retired; the first CalVer release has to come through `prepare`."""
    result, outputs = _read_version(tmp_path, python_shim, "0.1.2")

    assert result.returncode == 1
    assert "'0.1.2' is not CalVer" in result.stderr
    assert "prepare" in result.stderr
    assert outputs == {}


# --------------------------------------------------------------------------
# Check manifest.json matches pyproject.toml — the sync gate the rubric promises
# --------------------------------------------------------------------------

def _pair(directory: Path, pyproject_version: str, manifest_version: str) -> None:
    (directory / "pyproject.toml").write_text(_pyproject(pyproject_version), encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps({"name": "goodreads-mcp", "version": manifest_version}) + "\n", encoding="utf-8"
    )


def test_the_sync_gate_passes_when_the_two_versions_agree(tmp_path: Path, python_shim: Path):
    _pair(tmp_path, "2.0.0", "2.0.0")

    result = _run(_body(_SYNC_STEP), tmp_path, path_dirs=[python_shim])

    assert result.returncode == 0, result.stderr
    assert "::error::" not in result.stdout


def test_the_sync_gate_fails_and_names_both_versions_on_a_mismatch(tmp_path: Path, python_shim: Path):
    """This is the whole of what `docs/review-rubric.md` calls "release CI fails"."""
    _pair(tmp_path, "2.0.0", "1.9.0")

    result = _run(_body(_SYNC_STEP), tmp_path, path_dirs=[python_shim])

    assert result.returncode == 1
    assert "::error::manifest.json version (1.9.0) != pyproject.toml version (2.0.0)" in result.stdout


def test_the_sync_gate_fails_on_a_bumped_pyproject_with_a_stale_manifest(
    tmp_path: Path, python_shim: Path
):
    """The realistic direction of the mistake: bump one file, forget the other."""
    _pair(tmp_path, "0.1.2", "0.1.1")

    result = _run(_body(_SYNC_STEP), tmp_path, path_dirs=[python_shim])

    assert result.returncode == 1


def test_the_committed_tree_passes_its_own_sync_gate(tmp_path: Path, python_shim: Path):
    """Run the real step against the real files, not a paraphrase of it."""
    shutil.copy2(_PYPROJECT, tmp_path / "pyproject.toml")
    shutil.copy2(_MANIFEST, tmp_path / "manifest.json")

    result = _run(_body(_SYNC_STEP), tmp_path, path_dirs=[python_shim])

    assert result.returncode == 0, (
        "pyproject.toml and manifest.json have drifted apart; release CI would refuse this tree:\n"
        + result.stdout
    )


def test_the_rubric_still_promises_the_gate_this_file_exercises():
    """If the promise is reworded away, the tests above are guarding nothing stated."""
    prose = re.sub(r"\s+", " ", _RUBRIC.read_text(encoding="utf-8"))
    assert "`pyproject.toml` and `manifest.json` versions in sync — release CI fails" in prose
    assert "exit 1" in _step(_SYNC_STEP).run


# --------------------------------------------------------------------------
# The manifest names this fork, not the project it was forked from (#96)
#
# Claude Desktop shows a bundle's author and homepage from the manifest, and
# `mcpb validate` checks neither, so this is the only thing holding them. The
# manifest shipped with upstream's author and links next to this fork's
# `GPL-3.0-only`: people who installed the bundle were sent to another project
# for support, and the relicense was attributed to an author who released
# under MIT. Upstream's credit belongs in LICENSE.MIT and the README, where
# the MIT licence requires it to stay.

_FORK_OWNER = "Danathar"
_FORK_URL = f"https://github.com/{_FORK_OWNER}/goodreads-mcp"
_UPSTREAM_URL = "https://github.com/shreeyachand/goodreads-mcp"
_README = _ROOT / "README.md"
_LICENSE_MIT = _ROOT / "LICENSE.MIT"


def _manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_the_manifest_author_is_this_forks_maintainer():
    author = _manifest()["author"]
    assert author["name"] == _FORK_OWNER
    assert author["url"] == f"https://github.com/{_FORK_OWNER}"


def test_the_manifest_homepage_and_repository_point_at_this_fork():
    manifest = _manifest()
    assert manifest["homepage"] == _FORK_URL
    assert manifest["repository"] == {"type": "git", "url": _FORK_URL}


def test_the_manifest_license_is_the_forks_and_its_links_are_not_upstreams():
    """The pairing that was wrong: upstream's identity under this fork's licence."""
    manifest = _manifest()
    assert manifest["license"] == "GPL-3.0-only"
    text = json.dumps(manifest)
    assert "shreeyachand" not in text and "Shreeya" not in text


def test_upstream_credit_stays_where_the_readme_says_it_lives():
    """Moving the manifest off upstream must not move the attribution too."""
    readme = _README.read_text(encoding="utf-8")
    assert _UPSTREAM_URL in readme
    assert "LICENSE.MIT" in readme
    assert "Shreeya Chand" in _LICENSE_MIT.read_text(encoding="utf-8")



# --------------------------------------------------------------------------
# Real git repositories for the steps that read tags and history
# --------------------------------------------------------------------------


def _git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit(work: Path, path: str, text: str) -> None:
    target = work / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(work, "add", path)
    _git(work, "commit", "--quiet", "-m", f"change {path}")


def _repo_with_remote(tmp_path: Path) -> Path:
    """A real work repo with a real `origin`, so `git ls-remote` and pushes really run."""
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch=main", str(bare)], check=True, capture_output=True
    )
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "--quiet", "--initial-branch=main")
    for path in ("goodreads_mcp/server.py", "docs/notes.md"):
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text("x\n", encoding="utf-8")
    shutil.copy2(_PYPROJECT, work / "pyproject.toml")
    shutil.copy2(_MANIFEST, work / "manifest.json")
    _git(work, "add", ".")
    _git(work, "commit", "--quiet", "-m", "initial")
    _git(work, "remote", "add", "origin", f"file://{bare}")
    _git(work, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    return work


def _remote_tags(work: Path) -> list[str]:
    return sorted(
        line.split("refs/tags/")[1]
        for line in _git(work, "ls-remote", "--tags", "origin").splitlines()
        if not line.endswith("^{}")
    )


# --------------------------------------------------------------------------
# Check the schedule is enabled
# --------------------------------------------------------------------------


def _enabled(tmp_path: Path, event: str, opted_in: str) -> tuple[dict[str, str], str]:
    output = tmp_path / "github_output"
    summary = tmp_path / "summary"
    result = _run(
        _body(_ENABLED_STEP),
        tmp_path,
        env={"EVENT_NAME": event, "OPTED_IN": opted_in, "GITHUB_STEP_SUMMARY": str(summary)},
        github_output=output,
    )
    assert result.returncode == 0, result.stderr
    return _outputs(output), summary.read_text(encoding="utf-8") if summary.exists() else ""


def test_a_scheduled_run_does_nothing_until_the_repository_opts_in(tmp_path: Path):
    outputs, summary = _enabled(tmp_path, "schedule", "false")
    assert outputs == {"go": "false"}
    assert "`AUTO_RELEASE_ENABLED` to `true`" in summary


def test_a_scheduled_run_proceeds_once_the_repository_opts_in(tmp_path: Path):
    assert _enabled(tmp_path, "schedule", "true")[0] == {"go": "true"}


@pytest.mark.parametrize("opted_in", ["true", "false"])
def test_a_manual_run_always_proceeds(tmp_path: Path, opted_in: str):
    assert _enabled(tmp_path, "workflow_dispatch", opted_in)[0] == {"go": "true"}


def test_the_opt_in_is_the_repository_variable():
    step = _step(_ENABLED_STEP)
    assert step.env["OPTED_IN"] == "${{ vars.AUTO_RELEASE_ENABLED == 'true' }}"
    assert step.env["EVENT_NAME"] == "${{ github.event_name }}"


# --------------------------------------------------------------------------
# Decide whether to release — only a change to the runtime package counts
# --------------------------------------------------------------------------


def _decide(work: Path, force: str = "") -> tuple[dict[str, str], str]:
    output = work / "github_output"
    summary = work / "summary"
    result = _run(
        _body(_DECIDE_STEP),
        work,
        env={"FORCE": force, "GITHUB_STEP_SUMMARY": str(summary)},
        github_output=output,
    )
    assert result.returncode == 0, result.stderr
    return _outputs(output), summary.read_text(encoding="utf-8") if summary.exists() else ""


def test_the_first_calver_release_goes_ahead_with_no_tags(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    assert _decide(work)[0] == {"go": "true"}


def test_the_v_prefixed_tags_of_the_retired_line_are_not_a_baseline(tmp_path: Path):
    """v0.1.1 is not a CalVer release, so the first CalVer release is still the first."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "v0.1.1")
    assert _decide(work)[0] == {"go": "true"}


def test_a_change_under_the_package_releases(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "2026.9.0")
    _commit(work, "goodreads_mcp/server.py", "changed\n")

    assert _decide(work)[0] == {"go": "true"}


def test_a_month_of_docs_tests_and_version_bumps_releases_nothing(tmp_path: Path):
    """A bump PR touches pyproject.toml and manifest.json; those must not count."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "2026.9.0")
    _commit(work, "docs/notes.md", "changed\n")
    _commit(work, "tests/test_new.py", "changed\n")
    _commit(work, "pyproject.toml", _PYPROJECT.read_text(encoding="utf-8") + "\n# bumped\n")
    _commit(work, "manifest.json", _MANIFEST.read_text(encoding="utf-8") + "\n")

    outputs, summary = _decide(work)

    assert outputs == {"go": "false"}
    assert "Nothing under `goodreads_mcp/` changed since **2026.9.0**" in summary
    assert "`force`" in summary


def test_force_releases_an_unchanged_package(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "2026.9.0")
    _commit(work, "docs/notes.md", "changed\n")

    assert _decide(work, force="true")[0] == {"go": "true"}


def test_the_baseline_is_the_newest_stable_tag_not_a_prerelease(tmp_path: Path):
    """A beta cut after the change must not hide that change from the stable release."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "2026.9.0")
    _commit(work, "goodreads_mcp/server.py", "changed\n")
    _git(work, "tag", "2026.10.0b1")

    assert _decide(work)[0] == {"go": "true"}


def test_the_baseline_is_sorted_as_versions_not_as_text(tmp_path: Path):
    """2026.10.0 is newer than 2026.9.0, though "1" sorts before "9"."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "2026.9.0")
    _commit(work, "goodreads_mcp/server.py", "changed\n")
    _git(work, "tag", "2026.10.0")

    assert _decide(work)[0] == {"go": "false"}


# --------------------------------------------------------------------------
# Require green checks on this commit
# --------------------------------------------------------------------------


def _check_runs(tmp_path: Path, runs: list[dict]) -> subprocess.CompletedProcess[str]:
    """Run the step with a `gh` that applies the step's own `--jq` to `runs`."""
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    (tmp_path / "runs.json").write_text(json.dumps({"check_runs": runs}), encoding="utf-8")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(
        stubs,
        "gh",
        'printf "%s\\n" "$2" > gh-path\n'
        'while [ "$#" -gt 0 ]; do [ "$1" = "--jq" ] && filter="$2"; shift; done\n'
        'exec jq -r "$filter" runs.json\n',
    )
    return _run(
        _body(_CHECKS_STEP),
        tmp_path,
        path_dirs=[stubs],
        env={"GH_TOKEN": "x", "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "a" * 40},
    )


def test_green_checks_let_the_release_through(tmp_path: Path):
    result = _check_runs(
        tmp_path,
        [
            {"name": "test", "status": "completed", "conclusion": "success"},
            {"name": "label", "status": "completed", "conclusion": "skipped"},
            {"name": "release", "status": "in_progress", "conclusion": None},
        ],
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "gh-path").read_text(encoding="utf-8").strip() == f"repos/o/r/commits/{'a' * 40}/check-runs"


def test_a_red_or_unfinished_check_stops_the_release(tmp_path: Path):
    result = _check_runs(
        tmp_path,
        [
            {"name": "test", "status": "completed", "conclusion": "failure"},
            {"name": "live", "status": "queued", "conclusion": None},
            {"name": "ok", "status": "completed", "conclusion": "success"},
        ],
    )

    assert result.returncode == 1
    assert "test: failure" in result.stderr
    assert "live: queued" in result.stderr
    assert "ok:" not in result.stderr


# --------------------------------------------------------------------------
# Validate the version
# --------------------------------------------------------------------------


def _validate(work: Path, version: str, prerelease: str = "false") -> subprocess.CompletedProcess[str]:
    return _run(_body(_VALIDATE_STEP), work, env={"VERSION": version, "PRERELEASE": prerelease})


@pytest.mark.parametrize(
    ("version", "prerelease", "ok"),
    [
        ("2026.10.0", "false", True),
        ("2026.10.0", "", True),  # a scheduled run has no inputs at all
        ("2026.10.0b1", "true", True),
        ("2026.10.0rc2", "true", True),
        ("2026.10.0b1", "false", False),
        ("2026.10.0rc1", "", False),
        ("2026.10.0", "true", False),
    ],
)
def test_the_suffix_and_the_prerelease_box_must_agree(tmp_path: Path, version: str, prerelease: str, ok: bool):
    work = _repo_with_remote(tmp_path)

    result = _validate(work, version, prerelease)

    assert (result.returncode == 0) is ok, result.stderr
    if not ok:
        assert "disagree" in result.stderr


def test_a_version_that_is_not_calver_is_refused(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    result = _validate(work, "0.1.2")
    assert result.returncode == 1
    assert "is not CalVer" in result.stderr


def test_an_existing_local_tag_is_refused(tmp_path: Path):
    """The tag being free is what proves the version was bumped."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "2026.10.0")

    result = _validate(work, "2026.10.0")

    assert result.returncode == 1
    assert "Tag 2026.10.0 already exists" in result.stderr


def test_a_tag_only_on_the_remote_is_refused(tmp_path: Path):
    """Another run can tag after this checkout was made."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "-a", "2026.10.0", "-m", "release")
    _git(work, "push", "--quiet", "origin", "2026.10.0")
    _git(work, "tag", "-d", "2026.10.0")

    result = _validate(work, "2026.10.0")

    assert result.returncode == 1
    assert "Tag 2026.10.0 already exists" in result.stderr


def test_a_longer_tag_on_the_remote_is_not_this_one(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "2026.1.10")
    _git(work, "push", "--quiet", "origin", "2026.1.10")
    _git(work, "tag", "-d", "2026.1.10")

    assert _validate(work, "2026.1.1").returncode == 0


def test_an_unreachable_remote_fails_instead_of_reading_as_no_tag(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    _git(work, "remote", "set-url", "origin", f"file://{tmp_path / 'gone.git'}")

    assert _validate(work, "2026.10.0").returncode != 0


@pytest.mark.parametrize(
    ("tags", "version", "prerelease", "ok"),
    [
        (["2026.10.0"], "2026.10.1", "false", True),
        (["2026.10.0"], "2026.11.0", "false", True),
        (["2026.10.0"], "2026.9.5", "false", False),
        (["2026.9.0", "2026.10.0"], "2026.9.9", "false", False),
        (["2026.9.9", "2026.9.10"], "2026.9.10", "false", False),  # exists
        (["2026.9.9", "2026.9.10"], "2026.9.11", "false", True),
        (["2026.10.0"], "2026.10.0b1", "true", False),  # a beta of a released version
        (["2026.10.0"], "2026.11.0b1", "true", True),
        (["2026.10.0", "2026.11.0b1"], "2026.11.0", "false", True),  # stable after its beta
        (["2026.10.0", "2026.12.0rc1"], "2026.11.0", "false", True),  # betas are no baseline
        (["v0.1.1", "v9.9.9"], "2026.10.0", "false", True),  # the retired line
    ],
)
def test_a_version_must_be_newer_than_the_latest_stable_release(
    tmp_path: Path, tags: list[str], version: str, prerelease: str, ok: bool
):
    work = _repo_with_remote(tmp_path)
    for tag in tags:
        _git(work, "tag", tag)

    result = _validate(work, version, prerelease)

    assert (result.returncode == 0) is ok, result.stderr


def test_the_refusal_names_both_versions(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "2026.10.0")

    result = _validate(work, "2026.9.5")

    assert "2026.9.5 is not newer than the latest release 2026.10.0" in result.stderr


# --------------------------------------------------------------------------
# Tag the approved commit
# --------------------------------------------------------------------------


def test_the_tag_step_pushes_an_annotated_tag_with_no_v_prefix(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    head = _git(work, "rev-parse", "HEAD").strip()

    result = _run(_body(_TAG_STEP), work, env={"VERSION": "2026.10.0", "GH_TOKEN": "x"})

    assert result.returncode == 0, result.stderr
    assert _remote_tags(work) == ["2026.10.0"]
    assert _git(work, "cat-file", "-t", "2026.10.0").strip() == "tag"
    assert _git(work, "rev-parse", "2026.10.0^{commit}").strip() == head


def test_the_tag_step_writes_no_branch(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    before = _git(work, "ls-remote", "--heads", "origin")

    _run(_body(_TAG_STEP), work, env={"VERSION": "2026.10.0", "GH_TOKEN": "x"})

    assert _git(work, "ls-remote", "--heads", "origin") == before


# --------------------------------------------------------------------------
# Summarise
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dry_run", "expected"),
    [("true", "Dry run: **2026.10.0** is valid"), ("false", "Released **2026.10.0**."), ("", "Released **2026.10.0**.")],
)
def test_the_summary_says_whether_anything_was_published(tmp_path: Path, dry_run: str, expected: str):
    summary = tmp_path / "summary"

    result = _run(
        _body(_SUMMARY_STEP),
        tmp_path,
        env={"VERSION": "2026.10.0", "DRY_RUN": dry_run, "GITHUB_STEP_SUMMARY": str(summary)},
    )

    assert result.returncode == 0, result.stderr
    assert expected in summary.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# prepare: Decide the next version
# --------------------------------------------------------------------------


def _next_version(
    tmp_path: Path, tags: list[str], given: str = "", month: str = "10"
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    work = _repo_with_remote(tmp_path)
    for tag in tags:
        _git(work, "tag", tag)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    # `date -u +%Y` and `date -u +%-m`, pinned so the test does not move with the calendar.
    _write_stub(stubs, "date", f'case "$2" in +%Y) echo 2026 ;; +%-m) echo {month} ;; *) exit 9 ;; esac\n')
    output = work / "github_output"
    result = _run(
        _PREPARE.body(_NEXT_STEP),
        work,
        path_dirs=[stubs],
        env={"GIVEN_VERSION": given},
        github_output=output,
    )
    return result, _outputs(output)


@pytest.mark.parametrize(
    ("tags", "month", "expected"),
    [
        ([], "10", "2026.10.0"),
        (["v0.1.1"], "10", "2026.10.0"),
        (["2026.9.0", "2026.9.4"], "10", "2026.10.0"),
        (["2026.10.0"], "10", "2026.10.1"),
        (["2026.10.0", "2026.10.1", "2026.10.2b1"], "10", "2026.10.2"),
        (["2026.10.9", "2026.10.10"], "10", "2026.10.11"),
        (["2026.10.0", "2026.11.0"], "1", "2026.1.0"),  # 2026.1.* is not 2026.10.*
        (["2026.10.0b1"], "10", "2026.10.0"),
    ],
)
def test_the_next_version_is_derived_from_the_date_and_the_tags(
    tmp_path: Path, tags: list[str], month: str, expected: str
):
    result, outputs = _next_version(tmp_path, tags, month=month)

    assert result.returncode == 0, result.stderr
    assert outputs == {"version": expected}


def test_an_explicit_version_is_used_as_given(tmp_path: Path):
    result, outputs = _next_version(tmp_path, ["2026.10.0"], given="2026.11.0rc1")

    assert result.returncode == 0, result.stderr
    assert outputs == {"version": "2026.11.0rc1"}


@pytest.mark.parametrize("given", ["2026.09.0", "v2026.10.0", "2026.10.0\nversion=x", "main; rm -rf /"])
def test_a_malformed_explicit_version_is_refused(tmp_path: Path, given: str):
    result, outputs = _next_version(tmp_path, [], given=given)

    assert result.returncode == 1
    assert "is not CalVer" in result.stderr
    assert outputs == {}


def test_prepare_refuses_a_version_that_is_already_tagged(tmp_path: Path):
    result, outputs = _next_version(tmp_path, ["2026.10.1"], given="2026.10.1")

    assert result.returncode == 1
    assert "Tag 2026.10.1 already exists" in result.stderr
    assert outputs == {}


# --------------------------------------------------------------------------
# prepare: Bump pyproject.toml and manifest.json
# --------------------------------------------------------------------------


def _bump(work: Path, python_shim: Path, version: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    pytest.importorskip("tomllib")
    return _run(_PREPARE.body(_BUMP_STEP), work, path_dirs=[python_shim], env={"VERSION": version})


def test_the_bump_changes_the_version_in_both_files_and_nothing_else(tmp_path: Path, python_shim: Path):
    import tomllib

    work = _repo_with_remote(tmp_path)

    result = _bump(work, python_shim, "2026.10.0")

    assert result.returncode == 0, result.stderr
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {**json.loads(_MANIFEST.read_text(encoding="utf-8")), "version": "2026.10.0"}
    before = _PYPROJECT.read_text(encoding="utf-8").splitlines()
    after = (work / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    changed = [b for a, b in zip(before, after, strict=True) if a != b]
    assert changed == ['version = "2026.10.0"'], changed
    assert tomllib.loads("\n".join(after))["project"]["version"] == "2026.10.0"
    assert result.stdout.count("+++ b/") == 2, "the step no longer shows the diff it made"


def test_the_bump_refuses_a_pyproject_without_a_project_version(tmp_path: Path, python_shim: Path):
    """The first `version =` after [project] belongs to another table here; tomllib catches it."""
    work = _repo_with_remote(tmp_path)
    _commit(work, "pyproject.toml", '[project]\nname = "x"\n\n[tool.other]\nversion = "1"\n')

    result = _bump(work, python_shim, "2026.10.0")

    assert result.returncode != 0
    assert "reads back as None" in result.stderr


def test_the_bump_refuses_a_pyproject_with_no_version_line(tmp_path: Path, python_shim: Path):
    work = _repo_with_remote(tmp_path)
    _commit(work, "pyproject.toml", '[project]\nname = "x"\n')

    result = _bump(work, python_shim, "2026.10.0")

    assert result.returncode != 0
    assert "no `version" in result.stderr


# --------------------------------------------------------------------------
# prepare: Open the pull request
# --------------------------------------------------------------------------


def _open_pr(work: Path, version: str) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    log = work.parent / "gh-argv"
    stubs = work.parent / "bin"
    stubs.mkdir(exist_ok=True)
    _write_stub(stubs, "gh", _recorder(log))
    result = _run(
        _PREPARE.body(_PR_STEP),
        work,
        path_dirs=[stubs],
        env={
            "VERSION": version,
            "GH_TOKEN": "x",
            "GITHUB_REF_NAME": "main",
            "GITHUB_STEP_SUMMARY": str(work.parent / "summary"),
        },
    )
    return result, _argv(log)


def test_the_pull_request_carries_the_bump_on_a_release_branch(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    main_before = _git(work, "ls-remote", "origin", "refs/heads/main")
    (work / "pyproject.toml").write_text(_pyproject("2026.10.0"), encoding="utf-8")
    (work / "manifest.json").write_text('{"version": "2026.10.0"}\n', encoding="utf-8")

    result, calls = _open_pr(work, "2026.10.0")

    assert result.returncode == 0, result.stderr
    assert _git(work, "ls-remote", "origin", "refs/heads/main") == main_before, "main was written"
    branch = _git(work, "ls-remote", "origin", "refs/heads/release/2026.10.0")
    assert branch, "release/2026.10.0 was not pushed"
    sha = branch.split()[0]
    assert _git(work, "log", "-1", "--format=%s", sha).strip() == "chore(release): 2026.10.0"
    assert sorted(_git(work, "diff", "--name-only", f"{sha}~1", sha).split()) == ["manifest.json", "pyproject.toml"]
    ((gh, *args),) = calls
    assert args[:2] == ["pr", "create"]
    assert args[args.index("--base") + 1] == "main"
    assert args[args.index("--head") + 1] == "release/2026.10.0"
    assert args[args.index("--title") + 1] == "chore(release): 2026.10.0"


def test_no_pull_request_when_the_files_are_already_at_the_version(tmp_path: Path):
    work = _repo_with_remote(tmp_path)

    result, calls = _open_pr(work, "2026.10.0")

    assert result.returncode == 1
    assert "already at 2026.10.0" in result.stderr
    assert calls == []
    assert not _git(work, "ls-remote", "origin", "refs/heads/release/*")


# --------------------------------------------------------------------------
# The workflow's shape: triggers, jobs, gates, and what reaches a shell
# --------------------------------------------------------------------------


def test_the_workflow_runs_monthly_and_by_hand_but_not_on_a_push():
    on_block = _RELEASE_TEXT.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert '- cron: "0 9 1 * *"' in on_block
    assert "workflow_dispatch:" in on_block
    assert "push:" not in on_block and "branches:" not in on_block
    inputs = re.findall(r"^      ([a-z_]+):$", on_block, re.M)
    assert inputs == ["prepare", "version", "prerelease", "force", "dry_run"]


def test_a_run_is_only_ever_one_of_the_two_jobs():
    assert "  prepare:\n    if: github.event_name == 'workflow_dispatch' && inputs.prepare\n" in _RELEASE_TEXT
    assert "  release:\n    if: github.event_name != 'workflow_dispatch' || !inputs.prepare\n" in _RELEASE_TEXT


def test_no_run_body_interpolates_an_expression():
    """`${{ }}` is pasted into the script before bash parses it; inputs go through env:."""
    for workflow in (_WORKFLOW, _PREPARE, _PUBLISH_PYPI):
        for step in workflow.steps:
            if step.run:
                assert "${{" not in step.run, f"{workflow.job}: {step.name!r} interpolates into its script"


def test_every_step_output_referenced_is_written_by_the_step_that_owns_it():
    """A reference to an output no body writes evaluates empty and skips the release silently."""
    seen = 0
    for workflow in (_WORKFLOW, _PREPARE):
        by_id = {step.id: step for step in workflow.steps if step.id}
        texts = [
            value
            for step in workflow.steps
            for value in (step.if_, step.run or "", *step.env.values(), *step.with_.values())
        ]
        references = set(_OUTPUT_REF.findall("\n".join(texts)))
        seen += len(references)
        for step_id, output in sorted(references):
            assert step_id in by_id, f"{workflow.job} reads steps.{step_id}.outputs.{output}, but no step has that id"
            body = by_id[step_id].run or ""
            assert f"{output}=" in body and "GITHUB_OUTPUT" in body, (
                f"steps.{step_id}.outputs.{output} is referenced but step {by_id[step_id].name!r} "
                f"never writes {output}= to $GITHUB_OUTPUT"
            )
    assert seen, "release.yml no longer wires any step output; this join is checking nothing"


def test_every_release_step_after_the_decision_is_gated_on_it():
    order = [step.name or step.uses for step in _STEPS]
    after = _STEPS[order.index(_DECIDE_STEP) + 1 :]
    assert _step(_DECIDE_STEP).if_ == "steps.enabled.outputs.go == 'true'"
    for step in after:
        if step.name == _CLEANUP_STEP:
            assert step.if_ == "always()"
            continue
        assert step.if_ in (_GO_IF, _WRITE_IF), f"step {step.name!r} is gated on {step.if_!r}"


def test_only_the_steps_that_write_are_skipped_on_a_dry_run():
    """A dry run still tests, packs and starts the bundle."""
    writers = [step.name for step in _STEPS if step.if_ == _WRITE_IF]
    assert writers == [_TAG_STEP, _PUBLISH_STEP]


def test_everything_is_checked_before_anything_is_written():
    order = [step.name for step in _STEPS]
    checks = [
        _CHECKS_STEP,
        _VERSION_STEP,
        _SYNC_STEP,
        _VALIDATE_STEP,
        _TEST_STEP,
        _PACK_STEP,
        _COMPILED_STEP,
        _LAUNCH_STEP,
    ]
    assert [order.index(name) for name in checks] == sorted(order.index(name) for name in checks)
    assert max(order.index(name) for name in checks) < order.index(_TAG_STEP) < order.index(_PUBLISH_STEP)


def test_the_publish_step_releases_the_bare_version_with_the_bundle():
    step = _step(_PUBLISH_STEP)
    assert step.uses.startswith("softprops/action-gh-release@")
    assert step.id == "publish"
    assert step.with_["tag_name"] == "${{ steps.version.outputs.version }}"
    assert step.with_["name"] == "${{ steps.version.outputs.version }}"
    assert step.with_["generate_release_notes"] == "true"
    assert step.with_["prerelease"] == "${{ inputs.prerelease == true }}"
    assert step.with_["files"] == "*.mcpb"


def test_the_cleanup_step_runs_even_when_the_release_failed():
    assert _step(_CLEANUP_STEP).if_ == "always()"


def test_prepare_never_writes_main():
    body = _PREPARE.step(_PR_STEP).run
    assert 'git push origin "HEAD:refs/heads/$branch"' in body
    assert 'branch="release/$VERSION"' in body
    pushes = [line for step in _PREPARE.steps for line in (step.run or "").splitlines() if "git push" in line]
    assert len(pushes) == 1, pushes


# --------------------------------------------------------------------------
# Install test deps / vendor / pack / clean up
# --------------------------------------------------------------------------


def test_the_test_step_installs_the_test_extra_before_running_pytest(tmp_path: Path):
    log = tmp_path / "argv"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "pip", _recorder(log))
    _write_stub(stubs, "pytest", _recorder(log))

    result = _run(_body(_TEST_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0, result.stderr
    calls = [record[1:] for record in _argv(log)]
    assert calls == [["install", "-e", ".[test]"], ["-q"]]


def test_a_failing_test_run_stops_the_release(tmp_path: Path):
    """`-e` is the only thing between a red suite and a published bundle."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "pip", "exit 0\n")
    _write_stub(stubs, "pytest", "exit 1\n")

    result = _run(_body(_TEST_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode != 0


def test_the_release_resolves_no_dependencies_ahead_of_the_user(tmp_path: Path):
    """No step installs the runtime dependencies into the tree that gets packed.

    That is what made v0.1.1 single-platform: `pip install --target ./vendor`
    on the runner picked the runner's wheels (#89). `uv run` in the manifest
    resolves `pyproject.toml` on the user's machine instead, so the release
    has nothing to vendor. Every `pip install` the workflow still runs is
    checked to be the test-extra install, into the runner's own environment.
    """
    commands = [
        line.strip()
        for step in _STEPS
        if step.run
        for line in step.run.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    installs = [line for line in commands if re.search(r"\bpip\b.*\binstall\b", line)]
    assert installs == ["python -m pip install --upgrade pip", 'pip install -e ".[test]"'], (
        f"release.yml installs something beyond the test extra: {installs}"
    )
    assert not [line for line in commands if "--target" in line], (
        "release.yml installs packages into the tree that gets packed"
    )


def test_the_pack_step_invokes_the_mcpb_packer_and_lists_the_bundle(tmp_path: Path):
    log = tmp_path / "argv"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "npx", _recorder(log) + "touch goodreads-mcp.mcpb\n")

    result = _run(_body(_PACK_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0, result.stderr
    assert [record[1:] for record in _argv(log)] == [["-y", _PACKER, "pack"]]
    assert "goodreads-mcp.mcpb" in result.stdout


def test_the_packer_is_pinned_to_the_version_ci_validates_with():
    """`npx -y @anthropic-ai/mcpb` unpinned runs whatever npm serves that minute (#175)."""
    assert re.fullmatch(r"@anthropic-ai/mcpb@\d+\.\d+\.\d+", _PACKER), _PACKER
    ci = _workflow_steps.Workflow(_ROOT / ".github" / "workflows" / "ci.yml", job="test")
    assert f"npx -y {_PACKER} validate" in ci.step("Validate MCPB manifest").run


def test_a_failed_pack_stops_the_release(tmp_path: Path):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "npx", "exit 1\n")

    result = _run(_body(_PACK_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode != 0


def test_the_pack_step_tolerates_a_packer_that_produced_nothing(tmp_path: Path):
    """Recorded, not endorsed: the listing falls through to `find`, so a packer
    that exits 0 without writing a bundle leaves the step green. The compiled-code
    check that follows it refuses to run with no bundle, which is where that
    release now stops."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "npx", "exit 0\n")

    result = _run(_body(_PACK_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0
    assert ".mcpb" not in result.stdout


# --------------------------------------------------------------------------
# Check the bundle carries no compiled code — the guard #89 asked for
# --------------------------------------------------------------------------


def _manifest_with_platforms(directory: Path, platforms: list[str]) -> None:
    """The real manifest, with only its platform list changed."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    manifest["compatibility"]["platforms"] = platforms
    (directory / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def _bundle(directory: Path, *names: str, manifest: Path = _MANIFEST) -> Path:
    """A `.mcpb` -- a zip -- holding the real manifest and the named empty files."""
    path = directory / "goodreads-mcp.mcpb"
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(manifest, "manifest.json")
        for name in names:
            archive.writestr(name, "")
    return path


_PURE = ("pyproject.toml", "goodreads_mcp/__init__.py", "goodreads_mcp/server.py")
_COMPILED_V011 = (
    "vendor/_cffi_backend.cpython-311-x86_64-linux-gnu.so",
    "vendor/cryptography/hazmat/bindings/_rust.abi3.so",
    "vendor/pydantic_core/_pydantic_core.cpython-311-x86_64-linux-gnu.so",
    "vendor/rpds/rpds.cpython-311-x86_64-linux-gnu.so",
)


def test_the_compiled_check_passes_a_pure_python_bundle_for_every_platform(
    tmp_path: Path, python_shim: Path
):
    _manifest_with_platforms(tmp_path, ["darwin", "win32", "linux"])
    _bundle(tmp_path, *_PURE)

    result = _run(_body(_COMPILED_STEP), tmp_path, path_dirs=[python_shim])

    assert result.returncode == 0, result.stderr
    assert "0 compiled" in result.stdout


def test_the_compiled_check_would_have_failed_the_v011_bundle(tmp_path: Path, python_shim: Path):
    """The four modules #89 found in the release, against the manifest's three platforms."""
    _manifest_with_platforms(tmp_path, ["darwin", "win32", "linux"])
    _bundle(tmp_path, *_PURE, *_COMPILED_V011)

    result = _run(_body(_COMPILED_STEP), tmp_path, path_dirs=[python_shim])

    assert result.returncode == 1
    assert "::error::" in result.stdout
    for module in _COMPILED_V011:
        assert module in result.stdout, f"the failure does not name {module}"


@pytest.mark.parametrize("extension", [".so", ".pyd", ".dylib"])
def test_the_compiled_check_recognises_every_native_module_suffix(
    tmp_path: Path, python_shim: Path, extension: str
):
    """`.so` is a Linux or macOS extension module, `.pyd` a Windows one, and
    `.dylib` a shared library a macOS wheel ships alongside its module."""
    _manifest_with_platforms(tmp_path, ["darwin", "win32"])
    _bundle(tmp_path, *_PURE, f"vendor/native{extension}")

    result = _run(_body(_COMPILED_STEP), tmp_path, path_dirs=[python_shim])

    assert result.returncode == 1


def test_the_compiled_check_allows_native_modules_in_a_single_platform_bundle(
    tmp_path: Path, python_shim: Path
):
    """A bundle that declares one platform may carry that platform's wheels.

    This is the narrowing #89 offered as the fallback, and the check leaves
    that door open rather than forbidding vendoring outright.
    """
    _manifest_with_platforms(tmp_path, ["linux"])
    _bundle(tmp_path, *_PURE, *_COMPILED_V011)

    result = _run(_body(_COMPILED_STEP), tmp_path, path_dirs=[python_shim])

    assert result.returncode == 0, result.stderr


def test_the_compiled_check_refuses_to_run_without_exactly_one_bundle(
    tmp_path: Path, python_shim: Path
):
    """No bundle is a packer that silently produced nothing; two is ambiguous."""
    _manifest_with_platforms(tmp_path, ["darwin", "win32", "linux"])

    result = _run(_body(_COMPILED_STEP), tmp_path, path_dirs=[python_shim])
    assert result.returncode == 1
    assert "::error::expected exactly one .mcpb" in result.stdout

    _bundle(tmp_path, *_PURE)
    shutil.copy2(tmp_path / "goodreads-mcp.mcpb", tmp_path / "goodreads-mcp-old.mcpb")

    result = _run(_body(_COMPILED_STEP), tmp_path, path_dirs=[python_shim])
    assert result.returncode == 1
    assert "::error::expected exactly one .mcpb" in result.stdout


def test_the_committed_manifest_declares_more_than_one_platform():
    """The compiled-code check only bites while this is true; say so if it stops being."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert len(manifest["compatibility"]["platforms"]) > 1, (
        "manifest.json now declares a single platform, so the compiled-code check in "
        "release.yml no longer guards against #89; narrow the check or the manifest on purpose"
    )


# --------------------------------------------------------------------------
# Start the packed bundle the way the manifest launches it
# --------------------------------------------------------------------------


def _launch(tmp_path: Path, python_shim: Path, uv_script: str) -> subprocess.CompletedProcess[str]:
    """Run the launch step against `tmp_path` with `uv_script` standing in for `uv`."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "uv", uv_script)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    return _run(
        _body(_LAUNCH_STEP),
        tmp_path,
        path_dirs=[stubs, python_shim],
        env={"RUNNER_TEMP": str(runner_temp)},
    )


def test_the_launch_step_runs_the_manifests_command_against_the_unpacked_bundle(
    tmp_path: Path, python_shim: Path
):
    """The argv is the committed manifest's `command` + `args`, `${__dirname}` and all.

    The test builds the expectation from the manifest the same way, so this
    is not a second copy of the launch command: it says the step runs what the
    manifest says, whatever that is.
    """
    _bundle(tmp_path, *_PURE)
    log = tmp_path / "argv"

    result = _launch(tmp_path, python_shim, _recorder(log))

    assert result.returncode == 0, result.stderr
    root = tmp_path / "runner-temp" / "bundle"
    config = json.loads(_MANIFEST.read_text(encoding="utf-8"))["server"]["mcp_config"]
    expected = [arg.replace("${__dirname}", str(root)) for arg in (config["command"], *config["args"])]
    (call,) = _argv(log)
    assert call[1:] == expected[1:]
    assert Path(call[0]).name == expected[0]
    assert "${__dirname}" not in " ".join(call), "the bundle root was not substituted"


def test_the_launch_step_unpacks_every_file_of_the_bundle_first(tmp_path: Path, python_shim: Path):
    """`uv run` reads pyproject.toml and the package out of the unpacked tree."""
    _bundle(tmp_path, *_PURE)

    result = _launch(tmp_path, python_shim, "exit 0\n")

    assert result.returncode == 0, result.stderr
    root = tmp_path / "runner-temp" / "bundle"
    for name in ("manifest.json", *_PURE):
        assert (root / name).is_file(), f"{name} was not unpacked before the launch"


def test_the_launch_step_reads_the_manifest_that_is_inside_the_bundle(tmp_path: Path, python_shim: Path):
    """The bundle's manifest, not the checkout's, is what a user's host will read."""
    inner = tmp_path / "inner-manifest.json"
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    manifest["server"]["mcp_config"]["args"] = ["run", "--directory", "${__dirname}", "python", "-c", "pass"]
    inner.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    _bundle(tmp_path, *_PURE, manifest=inner)
    log = tmp_path / "argv"

    result = _launch(tmp_path, python_shim, _recorder(log))

    assert result.returncode == 0, result.stderr
    (call,) = _argv(log)
    assert call[-2:] == ["-c", "pass"]


def test_the_launch_step_gives_the_server_an_empty_stdin(tmp_path: Path, python_shim: Path):
    """EOF is what makes a healthy stdio server exit; an open pipe would hang the release."""
    _bundle(tmp_path, *_PURE)

    result = _launch(tmp_path, python_shim, "cat > read.txt\n")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "read.txt").read_text(encoding="utf-8") == ""


def test_a_bundle_that_does_not_start_stops_the_release(tmp_path: Path, python_shim: Path):
    _bundle(tmp_path, *_PURE)

    result = _launch(tmp_path, python_shim, "echo 'ModuleNotFoundError: goodreads_mcp' >&2\nexit 1\n")

    assert result.returncode == 1
    assert "ModuleNotFoundError" in result.stderr


def test_the_launch_step_starts_from_a_clean_unpack_directory(tmp_path: Path, python_shim: Path):
    """A `.venv` left by an earlier run must not hide a bundle that no longer resolves."""
    _bundle(tmp_path, *_PURE)
    stale = tmp_path / "runner-temp" / "bundle" / ".venv"
    stale.mkdir(parents=True)
    (stale / "stale").write_text("", encoding="utf-8")

    result = _launch(tmp_path, python_shim, "exit 0\n")

    assert result.returncode == 0, result.stderr
    assert not stale.exists()


def test_the_release_installs_uv_before_it_starts_the_bundle():
    """The launch step's `uv` comes from the setup action, not from the runner image."""
    order = [step.name or step.uses for step in _STEPS]
    (setup,) = [step for step in _STEPS if step.uses.startswith("astral-sh/setup-uv@")]
    assert order.index(setup.name) < order.index(_LAUNCH_STEP)



# --------------------------------------------------------------------------
# PyPI (#153, #175)
# --------------------------------------------------------------------------


def _job_text(job: str) -> str:
    """One job's block of release.yml, from its key to the next job's."""
    match = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [a-z-]+:\n|\Z)", _RELEASE_TEXT, re.M | re.S)
    assert match, f"release.yml has no job named {job!r}"
    return match.group(1)


def test_the_pypi_build_writes_where_the_upload_reads(tmp_path: Path):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "uv", 'printf "%s\\n" "$@" > uv-args\n')

    result = _run(_BUILD_PYPI.body(_PYPI_BUILD_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "uv-args").read_text(encoding="utf-8").split() == ["build", "--out-dir", "dist"]
    assert _BUILD_PYPI.step(_PYPI_UPLOAD_STEP).with_["path"] == "dist/"
    assert _BUILD_PYPI.step(_PYPI_UPLOAD_STEP).with_["name"] == "pypi-dist"


def test_the_distributions_are_built_before_any_third_party_code_runs():
    """What Trusted Publishing signs is built in a job that ran nothing else (#175).

    The `release` job runs `pip install -e`, `npx ... pack` and `uv run` on
    unpinned closures, any of which can rewrite goodreads_mcp/ in that
    checkout. The build lives in its own job with its own checkout, and the
    only things that run there before `uv build` are the checkout, the uv
    setup action and hatchling.
    """
    assert [step.uses.split("@")[0] for step in _BUILD_PYPI.steps] == [
        "actions/checkout",
        "astral-sh/setup-uv",
        "",
        "actions/upload-artifact",
    ]
    assert _BUILD_PYPI.run_step_names() == {_PYPI_BUILD_STEP}
    assert _BUILD_PYPI.steps[0].with_["persist-credentials"] == "false"
    job = _job_text("build-pypi")
    assert "    permissions:\n      contents: read\n" in job
    # Same gate as `release`: the two run together, never beside `prepare`.
    assert re.search(r"^    if: (.+)$", job, re.M).group(1) == re.search(
        r"^    if: (.+)$", _job_text("release"), re.M
    ).group(1)


def test_a_failed_build_is_a_failed_release():
    """Nothing is tagged when there are no distributions to publish under the tag."""
    assert "    needs: build-pypi\n" in _job_text("release")


def test_the_release_job_persists_no_credentials():
    """Its `contents: write` token reaches the tag push and nothing else (#175).

    actions/checkout writes the job token into .git/config unless told not to,
    where every later step, including the unpinned packages the test, pack
    and launch steps install, can read it and push a tag or edit a release.
    """
    assert _STEPS[0].uses.startswith("actions/checkout@")
    assert _STEPS[0].with_["persist-credentials"] == "false"
    assert _step(_TAG_STEP).env["GH_TOKEN"] == "${{ github.token }}"
    # The steps that hold a token, by name: adding one is a visible change here.
    assert {step.name for step in _STEPS if "GH_TOKEN" in step.env} == {_CHECKS_STEP, _TAG_STEP}
    assert {step.name for step in _STEPS if "GITHUB_TOKEN" in step.env} == {_PUBLISH_STEP}


def test_the_tag_push_carries_the_header_the_checkout_did_not_persist(tmp_path: Path):
    """The push authenticates the way actions/checkout would have: one header, one command."""
    log = tmp_path / "argv"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "git", _recorder(log))

    result = _run(_body(_TAG_STEP), tmp_path, path_dirs=[stubs], env={"VERSION": "2026.10.0", "GH_TOKEN": "s3cret"})

    assert result.returncode == 0, result.stderr
    header = "AUTHORIZATION: basic " + base64.b64encode(b"x-access-token:s3cret").decode("ascii")
    pushes = [record[1:] for record in _argv(log) if "push" in record]
    assert pushes == [["-c", f"http.https://github.com/.extraheader={header}", "push", "origin", "refs/tags/2026.10.0"]]
    assert all("s3cret" not in field for record in _argv(log) for field in record if not field.startswith("http."))


def test_the_publish_job_runs_only_when_the_release_job_released():
    """Not on a dry run, a skipped month, or a refused version: only after a real release."""
    assert "      released: ${{ steps.publish.outcome == 'success' }}\n" in _RELEASE_TEXT
    assert _step(_PUBLISH_STEP).id == "publish"
    assert _step(_PUBLISH_STEP).if_ == _WRITE_IF
    assert [step.uses.split("@")[0] for step in _PUBLISH_PYPI.steps] == [
        "actions/download-artifact",
        "pypa/gh-action-pypi-publish",
    ]
    assert _PUBLISH_PYPI.step("Download the PyPI distributions").with_["name"] == "pypi-dist"
    assert "  publish-pypi:" in _RELEASE_TEXT
    job = _RELEASE_TEXT.split("  publish-pypi:\n", 1)[1]
    assert "    needs: [build-pypi, release]\n    if: needs.release.outputs.released == 'true'\n" in job


# --------------------------------------------------------------------------
# Clean up
# --------------------------------------------------------------------------


def test_the_cleanup_step_removes_every_bundle(tmp_path: Path):
    (tmp_path / "goodreads-mcp.mcpb").write_text("bundle\n", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("keep\n", encoding="utf-8")

    result = _run(_body(_CLEANUP_STEP), tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "goodreads-mcp.mcpb").exists()
    assert (tmp_path / "keep.txt").exists()


def test_the_cleanup_step_succeeds_when_there_is_nothing_to_clean(tmp_path: Path):
    """It runs on `always()`, including on the runs that never packed anything."""
    result = _run(_body(_CLEANUP_STEP), tmp_path)

    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# No silent gap
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("workflow", "executed"),
    [(_WORKFLOW, _EXECUTED), (_PREPARE, _PREPARE_EXECUTED), (_BUILD_PYPI, _BUILD_EXECUTED)],
    ids=["release", "prepare", "build-pypi"],
)
def test_every_run_step_in_the_release_workflow_is_executed_by_this_file(workflow, executed):
    """A new step must be run here, or listed here as deliberately not run."""
    present = workflow.run_step_names()
    assert present == executed, (
        f"release.yml job {workflow.job!r}'s run: steps and the set this file executes have diverged; "
        f"unrun: {sorted(present - executed)}, stale: {sorted(executed - present)}"
    )
