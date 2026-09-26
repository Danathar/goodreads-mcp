"""The metadata PyPI will show for this package (#153).

`goodreads-mcp` on PyPI is an unrelated project. Publishing, or telling users
to `uvx`, under that name would hand them someone else's code, so the
distribution name is pinned here along with the licence and links the
package page shows.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
_FORK_URL = "https://github.com/Danathar/goodreads-mcp"


def test_the_distribution_name_is_not_the_unrelated_pypi_project():
    assert _PROJECT["name"] == "goodreads-mcp-ai"


def test_the_import_package_and_console_script_keep_their_names():
    assert _PROJECT["scripts"]["goodreads-mcp"] == "goodreads_mcp.server:main"


def test_the_distribution_name_is_also_an_executable():
    """`uvx goodreads-mcp-ai` is what a client following the listing's `uvx` hint runs (#180).

    uv runs the executable named after the package and refuses when there is
    none of that name, so the distribution name is a second script for the
    same entry point.
    """
    assert _PROJECT["scripts"]["goodreads-mcp-ai"] == _PROJECT["scripts"]["goodreads-mcp"]


def test_the_licence_matches_the_manifest_and_ships_the_upstream_notice():
    assert _PROJECT["license"] == "GPL-3.0-only"
    assert _PROJECT["license-files"] == ["LICENSE", "LICENSE.MIT"]
    for name in _PROJECT["license-files"]:
        assert (_ROOT / name).is_file(), name


def test_the_package_page_links_point_at_this_fork():
    assert _PROJECT["readme"] == "README.md"
    urls = _PROJECT["urls"]
    assert urls["Homepage"] == _FORK_URL
    assert urls["Repository"] == _FORK_URL
    assert all(url.startswith(_FORK_URL) for url in urls.values())
