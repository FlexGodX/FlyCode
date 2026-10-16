"""Observer - builds a compact Observation from repository state (plan §7).

The full repository is never embedded in an observation. This module reads
git status, the file tree and the last known build/test result and produces
the aggregated, truncated view the rest of the system reasons about.
"""

from pathlib import Path

from ..domain import Observation
from ..domain.observation import MAX_FILES
from ..tools.filesystem import list_files
from ..tools.git import read_git_state

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}


def observe(
    repo_root: Path,
    task: str,
    task_id: str,
    step_number: int,
    *,
    build: dict | None = None,
    tests: dict | None = None,
    docker: dict | None = None,
    database: dict | None = None,
    services: dict | None = None,
    recent_logs: list[str] | None = None,
    recent_errors: list[str] | None = None,
    resource_state: dict | None = None,
    action_history_summary: dict | None = None,
    behavior_mode: str = "explore",
    modified_files: list[str] | None = None,
    created_files: list[str] | None = None,
    deleted_files: list[str] | None = None,
) -> Observation:
    """Assemble an Observation from current repository + tool state.

    `build`/`tests`/etc. are carried forward by the caller (SoftwareEnvironment)
    since they are the result of the *last* tool call, not something this
    function can discover by inspecting the filesystem alone.
    """
    listing = list_files(repo_root)
    file_tree = listing.payload.get("files", []) if listing.success else []

    git_state = read_git_state(repo_root)

    return Observation(
        task=task,
        task_id=task_id,
        step_number=step_number,
        git_state=git_state,
        file_tree=file_tree[:MAX_FILES],
        modified_files=modified_files or git_state.get("modified", []),
        created_files=created_files or [],
        deleted_files=deleted_files or [],
        build=build or {},
        tests=tests or {},
        docker=docker or {},
        database=database or {},
        services=services or {},
        recent_logs=recent_logs or [],
        recent_errors=recent_errors or [],
        resource_state=resource_state or {},
        action_history_summary=action_history_summary or {},
        behavior_mode=behavior_mode,
    )
