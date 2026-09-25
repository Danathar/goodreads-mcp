"""Read a workflow's steps, and run one the way the runner would.

This is the machinery `tests/test_release_workflow.py` grew for `release.yml`,
lifted out so a second workflow does not need a second copy of it. Nothing here
is a test; it is the reader and the runner the workflow tests share.

Two deliberate choices:

- **No PyYAML.** The test extra is `pytest` + `pytest-cov` and CI installs
  nothing else, so a step body is sliced out of the file by indentation rather
  than parsed. That keeps these tests runnable in the same environment the
  suite already has.
- **`bash -e`, not `bash -eo pipefail`.** That is the shell GitHub hands a
  `run:` body on Linux when the step (and the job, and the workflow) sets no
  `shell:`, which is true of every workflow in this repo. A step that wants
  `pipefail` has to say so itself, and running the body under a shell that
  quietly supplies it would hide exactly that mistake. Callers that model a
  step with an explicit `shell: bash` pass ``pipefail=True``.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

# `${{ ... }}`, the expressions GitHub substitutes before the body is a script.
EXPR = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")

# `steps.<id>.outputs.<name>`, wherever it appears in a workflow.
OUTPUT_REF = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")

_BLOCK_SCALAR = {"|", "|-", "|+", ">", ">-", ">+"}


class Step:
    """One entry of a `steps:` list, with only the keys these tests need."""

    def __init__(self, keys: dict[str, str], blocks: dict[str, dict[str, str]]):
        self.name = _unquote(keys.get("name", ""))
        self.id = keys.get("id", "").strip()
        self.if_ = keys.get("if", "").strip()
        self.uses = keys.get("uses", "").strip()
        self.run = keys.get("run")
        self.env = blocks.get("env", {})
        self.with_ = blocks.get("with", {})

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<step {self.name or self.uses!r}>"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _dedent(lines: list[str]) -> str:
    widths = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    if not widths:
        return ""
    cut = min(widths)
    return "\n".join(line[cut:] if line.strip() else "" for line in lines).rstrip() + "\n"


def _parse_step(block: list[str], item_indent: int) -> Step:
    """A step block, already sliced out of the file, as a key table.

    The block is normalised so the step's own keys sit at column 0: the first
    line loses its `- `, every other line loses the same indent.
    """
    norm: list[str] = []
    for i, line in enumerate(block):
        cut = item_indent + 2
        if not line.strip():
            norm.append("")
        elif i == 0:
            norm.append(line[cut:])
        else:
            norm.append(line[cut:] if len(line) >= cut else line.lstrip())

    keys: dict[str, str] = {}
    blocks: dict[str, dict[str, str]] = {}
    i = 0
    while i < len(norm):
        line = norm[i]
        i += 1
        if not line.strip() or line.startswith(" "):
            continue
        match = re.match(r"([A-Za-z_-]+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if value.strip() in _BLOCK_SCALAR or not value.strip():
            body: list[str] = []
            while i < len(norm) and (not norm[i].strip() or norm[i].startswith(" ")):
                body.append(norm[i])
                i += 1
            if not value.strip():
                # A nested mapping: `env:`, `with:`, and friends.
                mapping: dict[str, str] = {}
                for entry in body:
                    pair = re.match(r"\s+([A-Za-z0-9_.-]+):\s*(.*)$", entry)
                    if pair:
                        mapping[pair.group(1)] = _unquote(pair.group(2))
                blocks[key] = mapping
                keys.setdefault(key, "")
            elif key == "run":
                keys[key] = _dedent(body)
            else:
                # A folded scalar: `if: >-` and the like, joined back to one line.
                keys[key] = " ".join(entry.strip() for entry in body if entry.strip())
        elif key == "run":
            keys[key] = value.rstrip() + "\n"
        else:
            keys[key] = value
    return Step(keys, blocks)


class Workflow:
    """The `steps:` of one workflow job, keyed by step name.

    Without `job`, the file must have exactly one `steps:` list. Pass `job` to
    read one job's steps out of a multi-job file; the name must be a key
    directly under the top-level `jobs:`.
    """

    def __init__(self, path: Path, job: str | None = None):
        self.path = path
        self.job = job
        self.text = path.read_text(encoding="utf-8")
        self.steps = self._read_steps()

    def _job_lines(self) -> list[str]:
        lines = self.text.splitlines()
        if self.job is None:
            return lines
        (jobs_at,) = [i for i, line in enumerate(lines) if line.rstrip() == "jobs:"]
        job_indent: int | None = None
        start: int | None = None
        for i in range(jobs_at + 1, len(lines)):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if indent == 0:
                break
            if job_indent is None:
                job_indent = indent
            if start is not None and indent <= job_indent:
                return lines[start:i]
            if indent == job_indent and line.strip() == f"{self.job}:":
                start = i
        assert start is not None, f"{self.path.name} has no job named {self.job!r}"
        return lines[start:]

    def _read_steps(self) -> list[Step]:
        lines = self._job_lines()
        where = self.path.name if self.job is None else f"{self.path.name} job {self.job!r}"
        starts = [i for i, line in enumerate(lines) if line.strip() == "steps:"]
        assert len(starts) == 1, f"{where} no longer has exactly one steps: list"
        start = starts[0]
        list_indent = len(lines[start]) - len(lines[start].lstrip())

        blocks: list[list[str]] = []
        item_indent: int | None = None
        for line in lines[start + 1 :]:
            if not line.strip():
                if blocks:
                    blocks[-1].append(line)
                continue
            indent = len(line) - len(line.lstrip())
            if indent <= list_indent:
                break
            if line.lstrip().startswith("- "):
                if item_indent is None:
                    item_indent = indent
                if indent == item_indent:
                    blocks.append([line])
                    continue
            assert blocks, f"{self.path.name}: content before the first step entry"
            blocks[-1].append(line)

        assert item_indent is not None, f"{self.path.name}: steps: list is empty"
        return [_parse_step(block, item_indent) for block in blocks]

    def step(self, name: str) -> Step:
        matches = [step for step in self.steps if step.name == name]
        assert len(matches) == 1, f"{self.path.name} has {len(matches)} steps named {name!r}"
        return matches[0]

    def body(self, name: str, values: dict[str, str] | None = None) -> str:
        """The step's shell, with GitHub's expressions substituted.

        An expression this table does not name raises instead of being left in
        the script, so a new `${{ }}` cannot silently become literal text in a
        test.
        """
        values = values or {}
        body = self.step(name).run
        assert body, f"step {name!r} has no run: body"

        def replace(match: re.Match[str]) -> str:
            expression = match.group(1)
            if expression not in values:
                raise AssertionError(
                    f"step {name!r} uses ${{{{ {expression} }}}}, which this test does not supply"
                )
            return values[expression]

        return EXPR.sub(replace, body)

    def run_step_names(self) -> set[str]:
        return {step.name for step in self.steps if step.run}


# --------------------------------------------------------------------------
# Running a body
# --------------------------------------------------------------------------


def write_stub(directory: Path, name: str, script: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    path.chmod(0o755)
    return path


def recorder(log: Path) -> str:
    """Shell that appends one record per invocation, `$0` first.

    Fields are NUL-separated and records are terminated by an ASCII record
    separator, because an argument can itself be several lines long — a `gh
    issue create --body` is exactly that, and a newline-terminated record would
    read as one call per line of it.
    """
    return f'printf \'%s\\0\' "$0" "$@" >> "{log}"\nprintf \'\\36\' >> "{log}"\n'


def argv(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    records = []
    for record in log.read_text(encoding="utf-8").split("\36"):
        fields = [field for field in record.split("\0") if field]
        if fields:
            records.append(fields)
    return records


def run(
    body: str,
    cwd: Path,
    *,
    path_dirs: list[Path] | None = None,
    env: dict[str, str] | None = None,
    github_output: Path | None = None,
    pipefail: bool = False,
) -> subprocess.CompletedProcess[str]:
    script = cwd / "_step.sh"
    script.write_text(body, encoding="utf-8")
    environ = dict(os.environ)
    environ.pop("PYTHONPATH", None)
    if path_dirs:
        environ["PATH"] = os.pathsep.join(str(d) for d in path_dirs) + os.pathsep + environ["PATH"]
    if github_output is not None:
        github_output.touch()
        environ["GITHUB_OUTPUT"] = str(github_output)
    environ.update(env or {})
    flags = ["-e", "-o", "pipefail"] if pipefail else ["-e"]
    return subprocess.run(
        ["bash", "--noprofile", "--norc", *flags, str(script)],
        cwd=cwd,
        env=environ,
        capture_output=True,
        text=True,
    )


def outputs(github_output: Path) -> dict[str, str]:
    result = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key] = value
    return result
