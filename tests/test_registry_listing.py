"""The MCP registry listing, `server.json`, and the job that publishes it (#180).

The official registry (registry.modelcontextprotocol.io) is where MCP clients
and directories look for servers; a package on PyPI alone is not found. The
listing is accepted only if three things agree, and each is pinned here
because the registry checks them at publish time, after the release is
already tagged and on PyPI:

* the README the PyPI package carries names the server, as
  `mcp-name: <server.json name>` followed by a boundary;
* the package the listing points at is the one `pyproject.toml` builds, and
  `uvx <identifier>` (the command a client builds from `runtimeHint`) runs an
  executable the package really ships;
* the version the `publish-registry` job writes into its copy of
  `server.json` is the one the release job tagged, in both version fields.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tomllib

import pytest

import _workflow_steps

_ROOT = Path(__file__).resolve().parent.parent
_SERVER = json.loads((_ROOT / "server.json").read_text(encoding="utf-8"))
_PROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
_MANIFEST = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
_README = (_ROOT / "README.md").read_text(encoding="utf-8")
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_JOB = _workflow_steps.Workflow(_RELEASE, job="publish-registry")

_VERSION_STEP = "Write the released version into a copy of server.json"
_WAIT_STEP = "Wait for PyPI to serve the release"


def test_the_readme_carries_the_ownership_token_the_registry_looks_for():
    """`mcp-name: <name>` must be followed by whitespace, a tag or `-->`, or it is read as a prefix."""
    name = _SERVER["name"]
    assert re.search(rf"mcp-name: {re.escape(name)}(?=\s|<|-->)", _README), (
        f"README.md must carry 'mcp-name: {name}' on its own line or inside an HTML comment; "
        "the registry reads the PyPI description and refuses the listing without it"
    )
    assert _PROJECT["readme"] == "README.md", "the token is only seen if README.md is the PyPI description"


def test_the_listing_is_under_the_namespace_a_repository_token_is_granted():
    """GitHub OIDC login grants `io.github.<repository owner>/*`, matched case-sensitively."""
    assert _SERVER["name"] == "io.github.Danathar/goodreads-mcp"
    assert _SERVER["repository"]["url"] == _PROJECT["urls"]["Repository"]


def test_the_listing_points_at_the_package_pyproject_builds():
    (package,) = _SERVER["packages"]
    assert package["registryType"] == "pypi"
    assert package["registryBaseUrl"] == "https://pypi.org"
    assert package["identifier"] == _PROJECT["name"]
    assert package["transport"] == {"type": "stdio"}
    assert package["runtimeHint"] == "uvx"
    # `uvx goodreads-mcp-ai` runs the executable of that name; uv refuses when
    # the package ships none, so the identifier has to be a console script.
    assert package["identifier"] in _PROJECT["scripts"]


def test_the_listing_describes_the_same_server_as_the_bundle():
    assert _SERVER["description"] == _PROJECT["description"] == _MANIFEST["description"]
    assert _SERVER["title"] == _MANIFEST["display_name"]
    (package,) = _SERVER["packages"]
    variables = {v["name"]: v for v in package["environmentVariables"]}
    # The one setting the server reads, and the one the bundle's user_config passes.
    assert set(variables) == set(_MANIFEST["server"]["mcp_config"]["env"])
    assert variables["GOODREADS_USER_ID"]["isRequired"] is False


def test_the_tree_carries_a_placeholder_version_and_the_job_writes_the_real_one():
    """pyproject.toml is the one definition of a release number; server.json copies it at publish time."""
    assert _SERVER["version"] == "0.0.0"
    assert all(package["version"] == "0.0.0" for package in _SERVER["packages"])
    body = _JOB.step(_VERSION_STEP).run
    assert _JOB.step(_VERSION_STEP).env["VERSION"] == "${{ needs.release.outputs.version }}"
    assert "      version: ${{ steps.version.outputs.version }}\n" in _RELEASE.read_text(encoding="utf-8")
    assert "server.json > server.release.json" in body, "the tree's server.json must not be rewritten"


def test_the_version_step_sets_both_version_fields(tmp_path: Path):
    if shutil.which("jq") is None:
        pytest.skip("jq is not installed")
    shutil.copy2(_ROOT / "server.json", tmp_path / "server.json")

    result = _workflow_steps.run(_JOB.step(_VERSION_STEP).run, tmp_path, env={"VERSION": "2026.10.0"})

    assert result.returncode == 0, result.stderr
    released = json.loads((tmp_path / "server.release.json").read_text(encoding="utf-8"))
    assert released["version"] == "2026.10.0"
    assert [package["version"] for package in released["packages"]] == ["2026.10.0"]
    assert {**released, "version": "0.0.0", "packages": _SERVER["packages"]} == _SERVER, "only the versions change"
    assert json.loads((tmp_path / "server.json").read_text(encoding="utf-8")) == _SERVER


def test_the_wait_step_gives_up_rather_than_publishing_an_unvalidatable_listing(tmp_path: Path):
    """The registry validates against PyPI's version page; a listing published before it exists is refused anyway."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    # curl that never finds the page, and a sleep that does not.
    _workflow_steps.write_stub(stubs, "curl", "exit 22\n")
    _workflow_steps.write_stub(stubs, "sleep", "exit 0\n")

    result = _workflow_steps.run(_JOB.step(_WAIT_STEP).run, tmp_path, path_dirs=[stubs], env={"VERSION": "2026.10.0"})

    assert result.returncode == 1
    assert "::error::PyPI did not serve goodreads-mcp-ai 2026.10.0" in result.stderr


def test_the_wait_step_stops_as_soon_as_pypi_serves_the_version(tmp_path: Path):
    log = tmp_path / "argv"
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _workflow_steps.write_stub(stubs, "curl", _workflow_steps.recorder(log))
    _workflow_steps.write_stub(stubs, "sleep", "exit 1\n")

    result = _workflow_steps.run(_JOB.step(_WAIT_STEP).run, tmp_path, path_dirs=[stubs], env={"VERSION": "2026.10.0"})

    assert result.returncode == 0, result.stdout + result.stderr
    (record,) = _workflow_steps.argv(log)
    assert record[-1] == "https://pypi.org/pypi/goodreads-mcp-ai/2026.10.0/json"


def test_the_job_runs_after_pypi_and_holds_only_what_publishing_needs():
    text = _RELEASE.read_text(encoding="utf-8")
    job = text.split("\n  publish-registry:\n", 1)[1]
    assert "    needs: [release, publish-pypi]\n    if: needs.release.outputs.released == 'true'\n" in job
    assert "    permissions:\n      id-token: write\n      contents: read\n" in job
    assert _JOB.steps[0].uses.startswith("actions/checkout@")
    assert _JOB.steps[0].with_["persist-credentials"] == "false"
    assert [step.name for step in _JOB.steps[1:]] == [
        _WAIT_STEP,
        _VERSION_STEP,
        "Install mcp-publisher",
        "Log in to the MCP registry",
        "Publish to the MCP registry",
    ]
    assert _JOB.step("Log in to the MCP registry").run.strip() == "./mcp-publisher login github-oidc"
    assert "./mcp-publisher publish server.release.json" in _JOB.step("Publish to the MCP registry").run
    for step in _JOB.steps:
        if step.run:
            assert "${{" not in step.run, f"{step.name!r} interpolates into its script"
