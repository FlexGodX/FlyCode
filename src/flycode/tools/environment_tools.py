"""Environment tools - Docker/Postgres/Alembic/HTTP (plan §50).

These reach outside the sandboxed repository into infrastructure the agent is
trying to bring up (a docker-compose stack, a database, a web server). Unlike
the MVP filesystem tools, failures here are expected and routine (the daemon
isn't running yet, the DB isn't up yet) - every function degrades to a
`ToolResult(success=False, ...)` rather than raising, so the agent can react
to "postgres isn't up" as an observation instead of a crash.
"""

import json
import subprocess
import sys
from pathlib import Path

from .filesystem import ToolResult

DEFAULT_TIMEOUT = 30.0

#: Alembic subcommands the agent may run (plan §50). Everything else is
#: rejected before reaching a subprocess.
ALEMBIC_SUBCOMMANDS: frozenset[str] = frozenset(
    {"current", "history", "heads", "upgrade", "downgrade", "revision", "stamp"}
)

#: Subcommands that take a revision argument.
ALEMBIC_REVISION_SUBCOMMANDS: frozenset[str] = frozenset({"upgrade", "downgrade", "stamp"})


def docker_compose(repo_root: Path, action: str = "ps", service: str | None = None, **_: object) -> ToolResult:
    if action not in {"up", "down", "ps", "restart", "stop", "build"}:
        return ToolResult(success=False, error_message=f"unsupported compose subcommand '{action}'")
    args = ["docker", "compose", action]
    if action == "up":
        args.extend(["--build", "--wait", "--wait-timeout", "60"])
    elif action == "ps":
        args.extend(["--all", "--format", "json"])
    if service:
        args.append(service)

    try:
        proc = subprocess.run(args, cwd=repo_root, capture_output=True, text=True, check=False,
                              timeout=300.0 if action == "up" else DEFAULT_TIMEOUT)
    except FileNotFoundError:
        return ToolResult(success=False, error_message="docker is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, error_message="docker compose timed out")

    running = action == "up" and proc.returncode == 0
    if action == "ps" and proc.returncode == 0:
        try:
            raw = proc.stdout.strip()
            containers = json.loads(raw) if raw.startswith("[") else [json.loads(s) for s in raw.splitlines()]
            running = bool(containers) and all(
                c.get("State") == "running" and c.get("Health", "") in ("", "healthy")
                for c in containers
            )
        except (ValueError, TypeError, AttributeError):
            return ToolResult(success=False, stdout=proc.stdout, error_message="invalid compose ps response")
    return ToolResult(
        success=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error_message="" if proc.returncode == 0 else proc.stderr.strip() or proc.stdout.strip() or "docker compose failed",
        payload={"action": action, "returncode": proc.returncode, "running": running},
    )


def docker_logs(repo_root: Path, service: str, tail: int = 100, **_: object) -> ToolResult:
    try:
        proc = subprocess.run(
            ["docker", "compose", "logs", "--tail", str(tail), service],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=DEFAULT_TIMEOUT,
        )
    except FileNotFoundError:
        return ToolResult(success=False, error_message="docker is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, error_message="docker compose logs timed out")

    return ToolResult(success=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr,
                      error_message="" if proc.returncode == 0 else proc.stderr.strip() or "docker logs failed")


def postgres_status(
    repo_root: Path,
    host: str = "localhost",
    port: int = 5432,
    user: str = "postgres",
    password: str = "",
    database: str = "postgres",
    service: str | None = None,
    **_: object,
) -> ToolResult:
    if service:
        try:
            proc = subprocess.run(
                ["docker", "compose", "exec", "-T", service, "pg_isready", "-U", user, "-d", database],
                cwd=repo_root, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return ToolResult(success=False, error_message=str(exc), payload={"connected": False})
        return ToolResult(success=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr,
                          payload={"connected": proc.returncode == 0},
                          error_message="" if proc.returncode == 0 else proc.stderr.strip() or proc.stdout.strip())
    try:
        import psycopg2
    except ImportError:
        return ToolResult(success=False, error_message="psycopg2 is not installed")

    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password, dbname=database, connect_timeout=3
        )
        conn.close()
    except Exception as exc:  # noqa: BLE001 - connection failures are routine data, not bugs
        return ToolResult(success=False, error_message=str(exc), payload={"connected": False})

    return ToolResult(success=True, payload={"connected": True})


def postgres_query(
    repo_root: Path,
    query: str,
    host: str = "localhost",
    port: int = 5432,
    user: str = "postgres",
    password: str = "",
    database: str = "postgres",
    **_: object,
) -> ToolResult:
    try:
        import psycopg2
    except ImportError:
        return ToolResult(success=False, error_message="psycopg2 is not installed")

    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password, dbname=database, connect_timeout=3
        )
        try:
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall() if cur.description else []
                columns = [c.name for c in cur.description] if cur.description else []
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return ToolResult(success=False, error_message=str(exc))

    return ToolResult(success=True, payload={"rows": rows, "columns": columns})


def alembic(repo_root: Path, action: str = "current", revision: str = "head",
            service: str | None = None, **_: object) -> ToolResult:
    """Run an alembic subcommand against the repo's migration chain (plan §50).

    A broken migration chain, a missing `alembic` binary or a misconfigured
    `alembic.ini` are routine data ([`ToolResult(success=False, ...)`]), not
    exceptions.
    """
    if action not in ALEMBIC_SUBCOMMANDS:
        return ToolResult(
            success=False,
            error_message=f"unsupported alembic subcommand '{action}'",
            payload={"action": action, "subcommands": sorted(ALEMBIC_SUBCOMMANDS)},
        )

    args = (["docker", "compose", "exec", "-T", service, "python", "-m", "alembic"]
            if service else [sys.executable, "-m", "alembic"])
    args.append(action)
    if action in ALEMBIC_REVISION_SUBCOMMANDS:
        args.append(revision)

    try:
        proc = subprocess.run(args, cwd=repo_root, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT, check=False)
    except FileNotFoundError:
        return ToolResult(success=False, error_message="alembic is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, error_message="alembic timed out")

    return ToolResult(
        success=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error_message="" if proc.returncode == 0 else proc.stderr.strip() or proc.stdout.strip() or "alembic failed",
        payload={"action": action, "revision": revision, "returncode": proc.returncode,
                 "migration_applied": proc.returncode == 0 and action == "upgrade" and revision == "head"},
    )


def http_request(
    repo_root: Path,
    url: str,
    method: str = "GET",
    timeout: float = 10.0,
    **_: object,
) -> ToolResult:
    try:
        import httpx
    except ImportError:
        return ToolResult(success=False, error_message="httpx is not installed")

    try:
        response = httpx.request(method, url, timeout=timeout)
    except (httpx.HTTPError, ValueError) as exc:
        return ToolResult(success=False, error_message=str(exc), payload={"url": url})

    return ToolResult(
        success=200 <= response.status_code < 300,
        stdout=response.text[:5000],
        payload={"status_code": response.status_code, "url": url},
        error_message="" if 200 <= response.status_code < 300 else f"HTTP {response.status_code}: {url}",
    )


def service_health(repo_root: Path, url: str, timeout: float = 5.0, **_: object) -> ToolResult:
    return http_request(repo_root, url=url, method="GET", timeout=timeout)
