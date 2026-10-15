"""Test runner tool - invokes pytest and parses a structured summary (plan §49)."""

import re
import subprocess
import sys
from pathlib import Path

from .filesystem import ToolResult

SUMMARY_RE = re.compile(
    r"(?P<passed>\d+) passed"
    r"|(?P<failed>\d+) failed"
    r"|(?P<errors>\d+) error"
)


def run_tests(repo_root: Path, target: str = "", timeout: float = 60.0,
              service: str | None = None, **_: object) -> ToolResult:
    args = (["docker", "compose", "exec", "-T", service, "python"] if service else [sys.executable])
    args.extend(["-m", "pytest", "-q"])
    if target:
        args.append(target)

    try:
        proc = subprocess.run(
            args,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return ToolResult(success=False, error_message=str(exc), payload={"status": "error", "errors": 1})
    except subprocess.TimeoutExpired as exc:
        return ToolResult(
            success=False,
            error_message="pytest timed out",
            payload={"status": "timeout", "passed": 0, "failed": 0, "errors": 1, "total": 0},
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
        )

    summary = _parse_summary(proc.stdout + "\n" + proc.stderr)
    summary["failures"] = _parse_failures(proc.stdout)
    status = "passed" if proc.returncode == 0 else "failed"
    if summary["total"] == 0 and proc.returncode not in (0, 1):
        status = "error"
    summary["status"] = status

    return ToolResult(
        success=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        payload=summary,
    )


def _parse_summary(text: str) -> dict:
    passed = failed = errors = 0
    for match in SUMMARY_RE.finditer(text):
        if match.group("passed"):
            passed = int(match.group("passed"))
        elif match.group("failed"):
            failed = int(match.group("failed"))
        elif match.group("errors"):
            errors = int(match.group("errors"))
    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total": passed + failed + errors,
    }


def _parse_failures(stdout: str) -> list[dict]:
    failures = []
    for line in stdout.splitlines():
        if line.startswith("FAILED "):
            name = line.removeprefix("FAILED ").split(" - ")[0].strip()
            failures.append({"test": name})
    return failures
