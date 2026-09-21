"""Git tools - status/diff as read-only observers of repository state (plan §49)."""

import subprocess
from pathlib import Path

from .filesystem import ToolResult


def _run(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )


def git_status(repo_root: Path, **_: object) -> ToolResult:
    proc = _run(repo_root, ["status", "--porcelain=v1", "--branch"])
    if proc.returncode != 0:
        return ToolResult(success=False, error_message=proc.stderr.strip())

    lines = proc.stdout.splitlines()
    branch = lines[0].removeprefix("## ") if lines else "unknown"
    modified, staged, untracked = [], [], []
    for line in lines[1:]:
        code, _, path = line.partition(" ")
        if code.strip() == "??":
            untracked.append(path)
        elif code.startswith(" "):
            modified.append(path)
        else:
            staged.append(path)

    payload = {
        "branch": branch,
        "dirty": bool(lines[1:]),
        "modified": modified,
        "staged": staged,
        "untracked": untracked,
    }
    return ToolResult(success=True, stdout=proc.stdout, payload=payload)


def git_diff(repo_root: Path, path: str | None = None, **_: object) -> ToolResult:
    args = ["diff"]
    if path:
        args.append(path)
    proc = _run(repo_root, args)
    if proc.returncode != 0:
        return ToolResult(success=False, error_message=proc.stderr.strip())
    return ToolResult(success=True, stdout=proc.stdout, payload={"diff": proc.stdout})


def read_git_state(repo_root: Path) -> dict:
    """Structured git state used to populate `Observation.git_state`."""
    result = git_status(repo_root)
    if not result.success:
        return {"dirty": False, "modified": [], "staged": [], "untracked": [], "branch": "unknown"}
    return result.payload
