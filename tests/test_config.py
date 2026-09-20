"""Offline tests for ``goodreads_mcp.config``.

``server.py`` evaluates ``load_user_id()`` at import, so anything it raises
kills the server before the MCP handshake and the client sees only a dead
process (#93). Every case here is a config file a user could plausibly write
-- the README value without its braces, a number without quotes, a file the
process cannot read -- and the contract is the same for all of them: never
raise, fall back to "no user id configured", and say why on stderr.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from goodreads_mcp import config


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    """Point ``CONFIG_PATH`` at a scratch file and clear the env override."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.delenv("GOODREADS_USER_ID", raising=False)
    return path


# ----------------------------------------------------------- happy paths


def test_missing_file_is_silently_unset(config_path, capsys):
    assert config.load_user_id() is None
    assert capsys.readouterr().err == ""


def test_string_user_id_is_returned(config_path, capsys):
    config_path.write_text('{"user_id": "12345678"}')
    assert config.load_user_id() == "12345678"
    assert capsys.readouterr().err == ""


def test_numeric_user_id_is_coerced_to_str(config_path):
    """``{"user_id": 12345678}`` must honour the ``str | None`` signature."""
    config_path.write_text('{"user_id": 12345678}')
    uid = config.load_user_id()
    assert uid == "12345678"
    assert isinstance(uid, str)


def test_env_var_wins_without_reading_the_file(config_path, monkeypatch):
    config_path.write_text('"not even an object"')
    monkeypatch.setenv("GOODREADS_USER_ID", "999")
    assert config.load_user_id() == "999"


# ------------------------------------------- files that used to crash import


@pytest.mark.parametrize(
    "body, shape",
    [
        ('"12345678"', "str"),  # the README value copied without its braces
        ("[1, 2]", "list"),
        ("42", "int"),
        ("null", "NoneType"),
    ],
)
def test_non_object_json_is_ignored_with_a_warning(config_path, capsys, body, shape):
    config_path.write_text(body)

    assert config._load_config_file() == {}
    assert config.load_user_id() is None

    err = capsys.readouterr().err
    assert str(config_path) in err
    assert f"got {shape}" in err
    assert '{"user_id": "12345678"}' in err  # tells the user what to write


def test_invalid_json_is_ignored_with_a_warning(config_path, capsys):
    config_path.write_text("{not json")

    assert config.load_user_id() is None

    err = capsys.readouterr().err
    assert str(config_path) in err
    assert "Expecting" in err  # the JSONDecodeError message, so the user can find the typo


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX file modes and a non-root user for mode 000 to deny reads",
)
def test_unreadable_file_is_ignored_with_a_warning(config_path, capsys):
    config_path.write_text('{"user_id": "12345678"}')
    config_path.chmod(0)
    try:
        assert config.load_user_id() is None
    finally:
        config_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    err = capsys.readouterr().err
    assert str(config_path) in err
    assert "Permission denied" in err


def test_directory_in_place_of_file_is_ignored_with_a_warning(config_path, capsys):
    """Any ``OSError`` counts, not only the permission case."""
    config_path.mkdir()

    assert config.load_user_id() is None
    assert str(config_path) in capsys.readouterr().err


# -------------------------------------------------- bad values inside an object


@pytest.mark.parametrize(
    "body, shape",
    [
        ('{"user_id": ["12345678"]}', "list"),
        ('{"user_id": {"id": 1}}', "dict"),
        ('{"user_id": true}', "bool"),
        ('{"user_id": 1.5}', "float"),
    ],
)
def test_non_scalar_user_id_is_ignored_with_a_warning(config_path, capsys, body, shape):
    config_path.write_text(body)

    assert config.load_user_id() is None

    err = capsys.readouterr().err
    assert '"user_id" must be a string' in err
    assert f"got {shape}" in err


@pytest.mark.parametrize("body", ['{"user_id": null}', '{"user_id": ""}', "{}"])
def test_empty_user_id_is_unset_without_a_warning(config_path, capsys, body):
    config_path.write_text(body)
    assert config.load_user_id() is None
    assert capsys.readouterr().err == ""
