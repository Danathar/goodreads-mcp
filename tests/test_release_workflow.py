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
files, with recording stubs on `PATH` for the tools that would reach the
network.

Three contracts here are load-bearing and were asserted by nothing:

- **The version-sync gate.** `docs/review-rubric.md` tells a reviewer that
  "`pyproject.toml` and `manifest.json` versions in sync — release CI fails",
  and `docs/risk-tiers.md` makes the paired bump a Tier 2 rule. That promise is
  five lines of shell in one step. It is exercised here both ways, and the
  committed tree is put through it.
- **The tag check.** Its two outputs gate the five steps that test, vendor,
  pack and publish. A typo in an output name does not fail anything — it
  quietly skips the release. Every `steps.<id>.outputs.<name>` reference in the
  file is therefore joined back to the body that writes it.
- **The vendored dependency list.** The vendor step hard-codes the runtime
  requirements a second time. If `pyproject.toml` gains a dependency and that
  step does not, the `.mcpb` ships without it and fails on the user's machine,
  which no test in this repo could see. The two lists are pinned to each other.

A `run:` step that is not in `_EXECUTED` below fails the last test in the file,
so a new step cannot be added without either running it or saying out loud that
it is not run.

The reader and the runner themselves live in `tests/_workflow_steps.py`, which
this file and `tests/test_nightly_compliance_workflow.py` share.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_PYPROJECT = _ROOT / "pyproject.toml"
_MANIFEST = _ROOT / "manifest.json"
_RUBRIC = _ROOT / "docs" / "review-rubric.md"

# Every step in release.yml that carries a `run:`. Names are the workflow's own.
_VERSION_STEP = "Read version from pyproject.toml"
_SYNC_STEP = "Read version from manifest.json"
_TAG_STEP = "Check if tag already exists (skip if version not bumped)"
_TEST_STEP = "Install test deps and run tests"
_VENDOR_STEP = "Vendor Python dependencies"
_PACK_STEP = "Pack MCPB"
_CLEANUP_STEP = "Clean up vendor (avoid committing)"
_PIP_STEP = "Upgrade pip"

_EXECUTED = {
    _PIP_STEP,
    _VERSION_STEP,
    _SYNC_STEP,
    _TAG_STEP,
    _TEST_STEP,
    _VENDOR_STEP,
    _PACK_STEP,
    _CLEANUP_STEP,
}

# The condition the five release-gated steps share, verbatim.
_GATE_IF = "steps.check_tag.outputs.exists == 'false' && steps.check_tag.outputs.remote_exists == 'false'"

_OUTPUT_REF = _workflow_steps.OUTPUT_REF

_WORKFLOW = _workflow_steps.Workflow(_RELEASE)
_STEPS = _WORKFLOW.steps
_step = _WORKFLOW.step
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
    away from the virtualenv the suite is running in.
    """
    directory = tmp_path_factory.mktemp("python-shim")
    _write_stub(directory, "python", f'exec "{sys.executable}" "$@"\n')
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
        env=env,
        github_output=github_output,
        pipefail=True,
    )


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
# Read version from pyproject.toml
# --------------------------------------------------------------------------


def _pyproject(version: str) -> str:
    return f'[project]\nname = "goodreads-mcp"\nversion = "{version}"\n'


def test_the_version_step_publishes_version_and_tag(tmp_path: Path, python_shim: Path):
    (tmp_path / "pyproject.toml").write_text(_pyproject("1.2.3"), encoding="utf-8")
    output = tmp_path / "github_output"

    result = _run(_body(_VERSION_STEP), tmp_path, path_dirs=[python_shim], github_output=output)

    assert result.returncode == 0, result.stderr
    assert _outputs(output) == {"version": "1.2.3", "tag": "v1.2.3"}
    assert "Version: 1.2.3" in result.stdout


def test_the_version_step_prefixes_the_tag_with_v_exactly_once(tmp_path: Path, python_shim: Path):
    """The tag is built here and consumed by three later steps; its shape is the contract."""
    (tmp_path / "pyproject.toml").write_text(_pyproject("0.1.1"), encoding="utf-8")
    output = tmp_path / "github_output"

    _run(_body(_VERSION_STEP), tmp_path, path_dirs=[python_shim], github_output=output)

    outputs = _outputs(output)
    assert outputs["tag"] == "v" + outputs["version"]


def test_the_version_step_falls_back_to_tomli_without_tomllib(tmp_path: Path, python_shim: Path):
    """The `|| python -c "import tomli"` leg exists for Python 3.10 and has never run.

    Shadowing `tomllib` on PYTHONPATH is what an interpreter older than 3.11
    looks like from inside this shell: the first command fails, its traceback
    goes to /dev/null, and the second has to produce the version.
    """
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "tomllib.py").write_text('raise ImportError("no tomllib on this interpreter")\n', encoding="utf-8")
    (shadow / "tomli.py").write_text(
        "import re\n"
        "def load(handle):\n"
        "    text = handle.read().decode()\n"
        '    version = re.search(r\'^version = "([^"]+)"\', text, re.M).group(1)\n'
        '    return {"project": {"version": version}}\n',
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(_pyproject("9.9.9"), encoding="utf-8")
    output = tmp_path / "github_output"

    result = _run(
        _body(_VERSION_STEP),
        tmp_path,
        path_dirs=[python_shim],
        env={"PYTHONPATH": str(shadow)},
        github_output=output,
    )

    assert result.returncode == 0, result.stderr
    assert _outputs(output) == {"version": "9.9.9", "tag": "v9.9.9"}


def test_the_version_step_fails_when_neither_toml_reader_exists(tmp_path: Path, python_shim: Path):
    """Both legs down must stop the release, not publish an empty version."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    for module in ("tomllib.py", "tomli.py"):
        (shadow / module).write_text('raise ImportError("gone")\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(_pyproject("1.0.0"), encoding="utf-8")
    output = tmp_path / "github_output"

    result = _run(
        _body(_VERSION_STEP),
        tmp_path,
        path_dirs=[python_shim],
        env={"PYTHONPATH": str(shadow)},
        github_output=output,
    )

    assert result.returncode != 0
    assert _outputs(output) == {}


# --------------------------------------------------------------------------
# Read version from manifest.json — the sync gate the rubric promises
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
# Check if tag already exists
# --------------------------------------------------------------------------


def _git(directory: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo_with_remote(tmp_path: Path) -> Path:
    """A real work repo with a real `origin`, so `git ls-remote` really runs."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare)], check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "--quiet")
    (work / "file.txt").write_text("x\n", encoding="utf-8")
    _git(work, "add", "file.txt")
    _git(work, "commit", "--quiet", "-m", "initial")
    _git(work, "remote", "add", "origin", f"file://{bare}")
    _git(work, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    return work


def _tag_check(work: Path, tag: str) -> dict[str, str]:
    output = work / "github_output"
    result = _run(
        _body(_TAG_STEP, {"steps.version.outputs.tag": tag}),
        work,
        github_output=output,
    )
    assert result.returncode == 0, result.stderr
    return _outputs(output)


def test_the_tag_check_reports_an_unreleased_version_as_releasable(tmp_path: Path):
    work = _repo_with_remote(tmp_path)

    assert _tag_check(work, "v1.0.0") == {"exists": "false", "remote_exists": "false"}


def test_the_tag_check_sees_a_local_tag_that_was_never_pushed(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "v1.0.0")

    assert _tag_check(work, "v1.0.0") == {"exists": "true", "remote_exists": "false"}


def test_the_tag_check_sees_a_tag_on_the_remote(tmp_path: Path):
    """`remote_exists` is the half that catches a release another run already made."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "v1.0.0")
    _git(work, "push", "--quiet", "origin", "v1.0.0")

    assert _tag_check(work, "v1.0.0") == {"exists": "true", "remote_exists": "true"}


def test_the_tag_check_sees_an_annotated_tag_on_the_remote(tmp_path: Path):
    """An annotated tag adds a `^{}` line to ls-remote; the `$` anchor must still match."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "-a", "v1.0.0", "-m", "release")
    _git(work, "push", "--quiet", "origin", "v1.0.0")

    assert _tag_check(work, "v1.0.0") == {"exists": "true", "remote_exists": "true"}


def test_the_tag_check_does_not_confuse_a_longer_tag_for_this_one(tmp_path: Path):
    """v0.1.10 on the remote must not read as v0.1.1 already released."""
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "v0.1.10")
    _git(work, "push", "--quiet", "origin", "v0.1.10")

    assert _tag_check(work, "v0.1.1") == {"exists": "false", "remote_exists": "false"}


def test_the_tag_check_announces_the_skip_it_is_about_to_cause(tmp_path: Path):
    work = _repo_with_remote(tmp_path)
    _git(work, "tag", "v1.0.0")
    output = work / "github_output"

    result = _run(
        _body(_TAG_STEP, {"steps.version.outputs.tag": "v1.0.0"}), work, github_output=output
    )

    assert "Tag v1.0.0 already exists" in result.stdout


# --------------------------------------------------------------------------
# The gating join: an `if:` may only name an output some body writes
# --------------------------------------------------------------------------


def test_every_step_output_referenced_anywhere_is_written_by_the_step_that_owns_it():
    """A reference to an output no body writes evaluates empty and skips the release silently."""
    text = _RELEASE.read_text(encoding="utf-8")
    references = set(_OUTPUT_REF.findall(text))
    assert references, "release.yml no longer wires any step output; this join is checking nothing"

    by_id = {step.id: step for step in _STEPS if step.id}
    for step_id, output in sorted(references):
        assert step_id in by_id, f"release.yml reads steps.{step_id}.outputs.{output}, but no step has id {step_id!r}"
        body = by_id[step_id].run or ""
        assert f'{output}=' in body and "GITHUB_OUTPUT" in body, (
            f"steps.{step_id}.outputs.{output} is referenced but step {by_id[step_id].name!r} "
            f"never writes {output}= to $GITHUB_OUTPUT"
        )


def test_the_release_steps_are_all_gated_on_both_halves_of_the_tag_check():
    """One half alone would re-release over a tag that exists only on the remote."""
    gated = [step for step in _STEPS if step.if_ and step.if_ != "always()"]
    assert [step.name for step in gated] == [
        _TEST_STEP,
        _VENDOR_STEP,
        _PACK_STEP,
        "Create GitHub Release and upload .mcpb",
    ]
    for step in gated:
        assert step.if_ == _GATE_IF, f"step {step.name!r} is gated on {step.if_!r}, not the shared condition"


def test_the_publish_step_uploads_the_tag_the_version_step_computed():
    step = _step("Create GitHub Release and upload .mcpb")
    assert step.uses.startswith("softprops/action-gh-release@")
    block = _RELEASE.read_text(encoding="utf-8")
    assert "tag_name: ${{ steps.version.outputs.tag }}" in block
    assert 'files: "*.mcpb"' in block


def test_the_cleanup_step_runs_even_when_the_release_failed():
    assert _step(_CLEANUP_STEP).if_ == "always()"


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


def test_the_vendor_step_targets_vendor_and_counts_what_it_got(tmp_path: Path):
    log = tmp_path / "argv"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(
        stubs,
        "pip",
        _recorder(log) + 'mkdir -p vendor/mcp vendor/httpx vendor/anyio\n',
    )

    result = _run(_body(_VENDOR_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0, result.stderr
    assert "vendored 3 top-level packages" in result.stdout
    (call,) = [record[1:] for record in _argv(log)]
    assert call[:3] == ["install", "--target", "./vendor"]


def test_the_vendor_step_bundles_exactly_the_runtime_dependencies(tmp_path: Path):
    """The step re-types the dependency list; a dep added to only one of them ships broken.

    The requirements are read off the pip invocation the step actually makes,
    not off the workflow text, so a change to how the specs are quoted is fine
    and a change to which specs are installed is not.
    """
    log = tmp_path / "argv"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "pip", _recorder(log) + "mkdir -p vendor\n")

    _run(_body(_VENDOR_STEP), tmp_path, path_dirs=[stubs])

    (call,) = [record[1:] for record in _argv(log)]
    vendored = call[3:]
    declared = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    assert sorted(vendored) == sorted(declared), (
        "release.yml vendors a different dependency set than pyproject.toml declares; "
        "the .mcpb would ship without one of them"
    )


def test_a_failed_vendor_install_stops_the_release(tmp_path: Path):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "pip", "exit 1\n")

    result = _run(_body(_VENDOR_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode != 0


def test_the_pack_step_invokes_the_mcpb_packer_and_lists_the_bundle(tmp_path: Path):
    log = tmp_path / "argv"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "npx", _recorder(log) + "touch goodreads-mcp.mcpb\n")

    result = _run(_body(_PACK_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0, result.stderr
    assert [record[1:] for record in _argv(log)] == [["-y", "@anthropic-ai/mcpb", "pack"]]
    assert "goodreads-mcp.mcpb" in result.stdout


def test_a_failed_pack_stops_the_release(tmp_path: Path):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "npx", "exit 1\n")

    result = _run(_body(_PACK_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode != 0


def test_the_pack_step_tolerates_a_packer_that_produced_nothing(tmp_path: Path):
    """Recorded, not endorsed: the listing falls through to `find`, so a packer
    that exits 0 without writing a bundle leaves the step green and the upload
    with nothing to match."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "npx", "exit 0\n")

    result = _run(_body(_PACK_STEP), tmp_path, path_dirs=[stubs])

    assert result.returncode == 0
    assert ".mcpb" not in result.stdout


def test_the_cleanup_step_removes_the_vendor_tree_and_every_bundle(tmp_path: Path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "httpx").mkdir()
    (tmp_path / "goodreads-mcp.mcpb").write_text("bundle\n", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("keep\n", encoding="utf-8")

    result = _run(_body(_CLEANUP_STEP), tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "vendor").exists()
    assert not (tmp_path / "goodreads-mcp.mcpb").exists()
    assert (tmp_path / "keep.txt").exists()


def test_the_cleanup_step_succeeds_when_there_is_nothing_to_clean(tmp_path: Path):
    """It runs on `always()`, including on the runs that never vendored anything."""
    result = _run(_body(_CLEANUP_STEP), tmp_path)

    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# No silent gap
# --------------------------------------------------------------------------


def test_every_run_step_in_the_release_workflow_is_executed_by_this_file():
    """A new step must be run here, or listed here as deliberately not run."""
    present = {step.name for step in _STEPS if step.run}
    assert present == _EXECUTED, (
        "release.yml's run: steps and the set this file executes have diverged; "
        f"unrun: {sorted(present - _EXECUTED)}, stale: {sorted(_EXECUTED - present)}"
    )


def test_the_workflow_still_publishes_on_a_push_to_main():
    """The trigger decides whether any of the above ever runs."""
    text = _RELEASE.read_text(encoding="utf-8")
    assert re.search(r"^on:\n(  .*\n)*?    branches: \[main\]$", text, re.M)
    assert "workflow_dispatch:" in text
    assert "contents: write" in text
