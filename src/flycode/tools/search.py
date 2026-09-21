"""Search tool - plain-text pattern search across the repository (plan §49)."""

import re
from pathlib import Path

from .filesystem import ToolResult, resolve_path

MAX_MATCHES = 200
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}


def search(repo_root: Path, pattern: str, path: str = ".", **_: object) -> ToolResult:
    if not pattern:
        return ToolResult(success=False, error_message="search requires 'pattern'")

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return ToolResult(success=False, error_message=f"invalid pattern: {exc}")

    try:
        base = resolve_path(repo_root, path)
    except ValueError as exc:
        return ToolResult(success=False, error_message=str(exc))

    root = repo_root.resolve()
    matches: list[dict] = []
    for file_path in sorted(base.rglob("*")):
        if not file_path.is_file() or any(part in SKIP_DIRS for part in file_path.parts):
            continue
        try:
            text = file_path.read_text(errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(
                    {
                        "file": str(file_path.relative_to(root)),
                        "line": line_no,
                        "text": line.strip()[:300],
                    }
                )
                if len(matches) >= MAX_MATCHES:
                    break
        if len(matches) >= MAX_MATCHES:
            break

    stdout = "\n".join(f"{m['file']}:{m['line']}: {m['text']}" for m in matches)
    return ToolResult(success=True, stdout=stdout, payload={"matches": matches})
