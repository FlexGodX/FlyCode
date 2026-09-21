"""Environment tools - Docker/Postgres/HTTP (plan §50).

These reach outside the sandboxed repository into infrastructure the agent is
trying to bring up (a docker-compose stack, a database, a web server). Unlike
the MVP filesystem tools, failures here are expected and routine (the daemon
isn't running yet, the DB isn't up yet) - every function degrades to a
`ToolResult(success=False, ...)` rather than raising, so the agent can react
to "postgres isn't up" as an observation instead of a crash.
"""

import subprocess
from pathlib import Path

from .filesystem import ToolResult

DEFAULT_TIMEOUT = 30.0


def docker_compose(repo_root: Path, action: str = "ps", service: str | None = None, **_: object) -> ToolResult:
    args = ["docker", "compose", action]
    if service:
        args.append(service)
    if action == "up" and "-d" not in args:
        args.append("-d")

    try:
        proc = subprocess.run(args, cwd=repo_root, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT)
    except FileNotFoundError:
        return ToolResult(success=False, error_message="docker is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, error_message="docker compose timed out")

    return ToolResult(
        success=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error_message="" if proc.returncode == 0 else proc.stderr.strip(),
        payload={"action": action, "returncode": proc.returncode},
    )


def docker_logs(repo_root: Path, service: str, tail: int = 100, **_: object) -> ToolResult:
    try:
        proc = subprocess.run(
            ["docker", "compose", "logs", "--tail", str(tail), service],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except FileNotFoundError:
        return ToolResult(success=False, error_message="docker is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, error_message="docker compose logs timed out")

    return ToolResult(success=proc.returncode == 0, stdout=proc.stdout, stderr=proc.stderr)


def postgres_status(
    repo_root: Path,
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
    except httpx.HTTPError as exc:
        return ToolResult(success=False, error_message=str(exc))

    return ToolResult(
        success=response.status_code < 400,
        stdout=response.text[:5000],
        payload={"status_code": response.status_code},
    )


def service_health(repo_root: Path, url: str, timeout: float = 5.0, **_: object) -> ToolResult:
    return http_request(repo_root, url=url, method="GET", timeout=timeout)
