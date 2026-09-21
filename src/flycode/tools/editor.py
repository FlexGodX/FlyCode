"""Editor tools - the only mutating filesystem tools (plan §49).

Kept separate from `filesystem.py` so the read/write boundary is explicit at
the module level: everything here can change the repository on disk.
"""

from pathlib import Path

from .filesystem import ToolResult, resolve_path


def create_file(repo_root: Path, path: str, content: str = "", **_: object) -> ToolResult:
    try:
        target = resolve_path(repo_root, path)
    except ValueError as exc:
        return ToolResult(success=False, error_message=str(exc))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return ToolResult(success=True, payload={"path": path, "created": True})


def edit_file(
    repo_root: Path,
    path: str,
    old_text: str | None = None,
    new_text: str = "",
    content: str | None = None,
    **_: object,
) -> ToolResult:
    """Edit a file either by full replacement (`content`) or substring swap."""
    try:
        target = resolve_path(repo_root, path)
    except ValueError as exc:
        return ToolResult(success=False, error_message=str(exc))
    if not target.is_file():
        return ToolResult(success=False, error_message=f"no such file: {path}")

    if content is not None:
        target.write_text(content)
        return ToolResult(success=True, payload={"path": path, "mode": "replace"})

    if old_text is None:
        return ToolResult(success=False, error_message="edit_file requires 'content' or 'old_text'")

    current = target.read_text()
    if old_text not in current:
        return ToolResult(success=False, error_message=f"old_text not found in {path}")
    target.write_text(current.replace(old_text, new_text, 1))
    return ToolResult(success=True, payload={"path": path, "mode": "patch"})
