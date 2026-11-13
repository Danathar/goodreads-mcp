"""Pin the `env` block in `.claude/settings.json` that makes git refuse bare repositories.

The block sets git's `safe.bareRepository` to `explicit` for every command an
agent runs. Without it, a bare repository committed as ordinary files in a
checked-out branch is picked up by the pre-approved history commands
(`git log -p`) once the agent's working directory is inside it, and its config
can name a program for git to run (`diff.*.textconv`, with `info/attributes`
choosing which files it applies to) with no permission prompt. The guard hook
cannot see that config, so git itself has to refuse the repository.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS = _ROOT / ".claude" / "settings.json"
_IDENT = ["-c", "user.name=t", "-c", "user.email=t@t"]


def _settings_env() -> dict:
    return json.loads(_SETTINGS.read_text(encoding="utf-8")).get("env", {})


def _base_env(home: Path) -> dict:
    """A clean git environment: no user or system config, no inherited GIT_* overrides."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def _git(cwd: Path, env: dict, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=check
    )


def _planted_clone(tmp: Path, marker: Path) -> Path:
    """Build a clone whose tree carries a committed bare repository ``evil/``.

    ``evil/`` has one commit and a ``diff.x.textconv`` program (applied to every
    file by ``info/attributes``) that writes ``marker``.
    """
    env = _base_env(tmp)
    seed, src, evil = tmp / "seed", tmp / "src", tmp / "src" / "evil"
    _git(tmp, env, "init", "-q", str(seed), check=True)
    (seed / "f.txt").write_text("hello\n")
    _git(seed, env, "add", "f.txt", check=True)
    _git(seed, env, *_IDENT, "commit", "-qm", "seed", check=True)
    _git(tmp, env, "init", "-q", str(src), check=True)
    _git(tmp, env, "clone", "-q", "--bare", str(seed), str(evil), check=True)
    (evil / "info").mkdir(exist_ok=True)
    (evil / "info" / "attributes").write_text("* diff=x\n")
    _git(evil, env, "config", "diff.x.textconv", f"touch '{marker}'; cat", check=True)
    _git(src, env, "add", "evil", check=True)
    _git(src, env, *_IDENT, "commit", "-qm", "x", check=True)
    _git(tmp, env, "clone", "-q", str(src), "victim", check=True)
    return tmp / "victim"


def test_settings_set_safe_bare_repository_explicit_for_agent_commands() -> None:
    assert _settings_env() == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.bareRepository",
        "GIT_CONFIG_VALUE_0": "explicit",
    }


def test_committed_bare_repository_cannot_make_git_log_or_show_run_a_program(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "ran"
    victim = _planted_clone(tmp_path, marker)
    agent_env = {**_base_env(tmp_path), **_settings_env()}
    commands = [["show"], ["log", "-p", "-1"]]

    # The planted program does run without the setting, so the checks below are real.
    for args in commands:
        _git(victim / "evil", _base_env(tmp_path), *args)
        assert marker.exists(), (
            f"precondition: without safe.bareRepository git {' '.join(args)} "
            "runs the planted textconv"
        )
        marker.unlink()

    for args in commands:
        for cwd, argv in (
            (victim, ["-C", "evil", *args]),
            (victim / "evil", args),
        ):
            result = _git(cwd, agent_env, *argv)
            assert result.returncode != 0, f"git {' '.join(argv)} should refuse the bare repository"
            assert "safe.bareRepository" in result.stderr
            assert not marker.exists(), f"git {' '.join(argv)} ran the planted program"

    # The checkout's own repository (.git/, not bare) still works with the setting.
    assert _git(victim, agent_env, "status", "--short").returncode == 0
    assert _git(victim, agent_env, "log", "-1", "--format=%s").stdout.strip() == "x"
