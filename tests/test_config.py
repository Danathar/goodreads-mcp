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


def test_no_home_directory_is_unset_with_a_warning(monkeypatch, capsys):
    """``Path.home()`` raises on a host with no HOME and no passwd entry; it
    ran at import and killed the server before the handshake (#361)."""
    monkeypatch.setattr(config, "CONFIG_PATH", None)
    monkeypatch.delenv("GOODREADS_USER_ID", raising=False)

    def no_home() -> None:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(config.Path, "home", staticmethod(no_home))

    assert config.load_user_id() is None
    assert "Could not determine home directory" in capsys.readouterr().err


def test_no_home_directory_still_honours_the_env_var(monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", None)
    monkeypatch.setenv("GOODREADS_USER_ID", "12345678")

    def no_home() -> None:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(config.Path, "home", staticmethod(no_home))

    assert config.load_user_id() == "12345678"


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


@pytest.mark.parametrize(
    "encoding",
    ["utf-8-sig", "utf-16", "utf-32"],
    ids=["utf8-bom", "utf16", "utf32"],
)
def test_file_saved_with_a_bom_or_wide_encoding_is_read(config_path, capsys, encoding):
    """Windows editors save UTF-8 with a BOM or UTF-16 (#212)."""
    config_path.write_bytes('{"user_id": "12345678"}'.encode(encoding))
    assert config.load_user_id() == "12345678"
    assert capsys.readouterr().err == ""


def test_env_var_wins_without_reading_the_file(config_path, monkeypatch):
    config_path.write_text('"not even an object"')
    monkeypatch.setenv("GOODREADS_USER_ID", "999")
    assert config.load_user_id() == "999"


@pytest.mark.parametrize(
    "body, expected",
    [('{"user_id": "12345678"}', "12345678"), (None, None)],
    ids=["file", "no-file"],
)
def test_empty_env_var_is_unset_and_the_file_is_read(config_path, monkeypatch, capsys, body, expected):
    """The bundle passes an unset "Goodreads User ID" setting as "" (#315).

    The manifest's empty default is only a fix because an empty
    ``GOODREADS_USER_ID`` counts as unset here; were it returned as the id,
    the config file would be ignored and every shelf call would fail again.
    """
    if body is not None:
        config_path.write_text(body)
    monkeypatch.setenv("GOODREADS_USER_ID", "")
    assert config.load_user_id() == expected
    assert capsys.readouterr().err == ""


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


def test_undecodable_bytes_are_ignored_with_a_warning(config_path, capsys):
    config_path.write_bytes(b"\x81\x82{")

    assert config.load_user_id() is None

    err = capsys.readouterr().err
    assert str(config_path) in err
    assert "not valid JSON" in err
    assert "can't decode byte 0x81" in err


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
