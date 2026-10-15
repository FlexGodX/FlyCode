"""MVP software tools (plan §49) - a plain function registry keyed by tool name.

Environment tools (docker/postgres/http/alembic/service_health, plan §50) are
registered lazily so importing this package never requires optional runtime
dependencies (a running docker daemon, psycopg2, network access) that the MVP
does not need.
"""

from pathlib import Path
from typing import Callable

from .editor import create_file, edit_file
from .filesystem import ToolResult, list_files, read_file
from .git import git_diff, git_status, read_git_state
from .search import search
from .tests import run_tests

ToolFn = Callable[..., ToolResult]

TOOL_REGISTRY: dict[str, ToolFn] = {
    "read_file": read_file,
    "list_files": list_files,
    "search": search,
    "git_status": git_status,
    "git_diff": git_diff,
    "run_tests": run_tests,
    "edit_file": edit_file,
    "create_file": create_file,
}


def register_environment_tools() -> None:
    """Add docker/postgres/http/alembic tools (plan §50) to the registry."""
    from . import environment_tools

    TOOL_REGISTRY.update(
        {
            "docker_compose": environment_tools.docker_compose,
            "docker_logs": environment_tools.docker_logs,
            "postgres_status": environment_tools.postgres_status,
            "postgres_query": environment_tools.postgres_query,
            "alembic": environment_tools.alembic,
            "http_request": environment_tools.http_request,
            "service_health": environment_tools.service_health,
        }
    )


def run_tool(tool: str, repo_root: Path, **arguments: object) -> ToolResult:
    """Dispatch a tool call by name (used by SoftwareEnvironment.step)."""
    fn = TOOL_REGISTRY.get(tool)
    if fn is None:
        return ToolResult(success=False, error_message=f"unknown tool '{tool}'")
    try:
        return fn(repo_root, **arguments)
    except TypeError as exc:
        return ToolResult(success=False, error_message=f"bad arguments for '{tool}': {exc}")


__all__ = [
    "ToolResult",
    "ToolFn",
    "TOOL_REGISTRY",
    "run_tool",
    "register_environment_tools",
    "read_file",
    "list_files",
    "search",
    "git_status",
    "git_diff",
    "read_git_state",
    "run_tests",
    "edit_file",
    "create_file",
]
