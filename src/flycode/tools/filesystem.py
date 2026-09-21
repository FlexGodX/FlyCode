"""Filesystem tools - read-only inspection within a task's repository root.

Every tool is a plain function `(repo_root, **arguments) -> ToolResult` so the
environment can dispatch on `ToolAction.tool` without a class hierarchy. Paths
are always resolved relative to `repo_root` and validated to stay inside it -
no tool may read or write outside the sandboxed repository.
"""

from dataclasses import dataclass, field
from pathlib import Path

MAX_READ_BYTES = 200_000


@dataclass
class ToolResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    payload: dict = field(default_factory=dict)
    error_message: str = ""


def resolve_path(repo_root: Path, relative: str) -> Path:
    """Resolve `relative` under `repo_root`, refusing to escape it."""
    candidate = (repo_root / relative).resolve()
    root = repo_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path '{relative}' escapes the repository root")
    return candidate


def read_file(repo_root: Path, path: str, **_: object) -> ToolResult:
    try:
        target = resolve_path(repo_root, path)
    except ValueError as exc:
        return ToolResult(success=False, error_message=str(exc))
    if not target.is_file():
        return ToolResult(success=False, error_message=f"no such file: {path}")
    data = target.read_text(errors="replace")[:MAX_READ_BYTES]
    return ToolResult(success=True, stdout=data, payload={"path": path, "content": data})


def list_files(repo_root: Path, path: str = ".", **_: object) -> ToolResult:
    try:
        target = resolve_path(repo_root, path)
    except ValueError as exc:
        return ToolResult(success=False, error_message=str(exc))
    if not target.is_dir():
        return ToolResult(success=False, error_message=f"no such directory: {path}")

    root = repo_root.resolve()
    entries = []
    for p in sorted(target.rglob("*")):
        if any(part in {".git", "__pycache__", ".venv", "node_modules"} for part in p.parts):
            continue
        entries.append(str(p.relative_to(root)))
    return ToolResult(success=True, stdout="\n".join(entries), payload={"files": entries})
