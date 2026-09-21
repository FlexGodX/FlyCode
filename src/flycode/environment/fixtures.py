"""Deterministic fixture repositories for SoftwareGym (plan §74).

Each fixture is materialized fresh into a destination directory so the same
task can be run repeatedly with byte-identical starting state (Phase 0
acceptance: "the same task reproducibly runs multiple times").
"""

import shutil
from pathlib import Path
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FixtureCategory(StrEnum):
    """Task categories for benchmarking."""

    SIMPLE_BUG = "simple_bugs"
    IMPORT_BUG = "import_bugs"
    REGRESSION = "regressions"
    DOCKER = "docker"
    DATABASE = "database"
    HTTP = "http"
    DELAYED_REWARD = "delayed_reward"
    NOISY_FEEDBACK = "noisy_feedback"
    NOVEL_REPO = "novel_repo"
    RESOURCE_CONSTRAINED = "resource_constrained"
    PARTIAL_OBSERVABILITY = "partial_observability"
    CONTEXT_REVERSAL = "context_reversal"


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class FixtureConfig(BaseModel):
    """Metadata about a fixture including its target category and broken components."""

    category: FixtureCategory = FixtureCategory.SIMPLE_BUG
    broken_components: list[str] = Field(default_factory=list)
    broken_file_paths: list[str] = Field(default_factory=list)
    broken_line_offsets: list[int] = Field(default_factory=list)
    broken_line_contents: list[str] = Field(default_factory=list)

    # Infrastructure targets
    docker: str = "default"
    docker_compose_file: str | None = None

    database: str = "none"  # "none", "sqlite", "postgres", "mysql", "mongodb"
    database_url: str | None = None
    migration_path: str | None = None
    migrations: list[dict] = Field(default_factory=list)  # [{"name": "001_init", "path": "migrations/001_init.sql"}]

    http: str = "none"  # "none", "httpbin", "custom"
    api_endpoint: str | None = None

    web_port: int = 8000

    # Expected broken file content markers (for deterministic injection)
    bug_markers: list[dict] = Field(default_factory=list)  # [{"file": "app/main.py", "line": 12, "marker": "# BUG HERE"}]


class MaterializeResult:
    """Result of materializing a fixture."""

    path: Path
    config: FixtureConfig
    docker_services: list[str] = []
    database_type: str = "none"
    api_endpoint: str | None = None
    health_check_url: str | None = None

    def __init__(
        self,
        path: Path,
        config: FixtureConfig,
        docker_services: list[str] | None = None,
        database_type: str = "none",
        api_endpoint: str | None = None,
        health_check_url: str | None = None,
    ) -> None:
        self.path = path
        self.config = config
        self.docker_services = docker_services or []
        self.database_type = database_type
        self.api_endpoint = api_endpoint
        self.health_check_url = health_check_url


def toy_arithmetic_bug(dest: Path) -> MaterializeResult:
    """A tiny package with one off-by-one bug and a failing pytest test.

    Category: "simple bug" (plan §64) - deterministic, no external services.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    _write(
        dest,
        "mathutils.py",
        '"""Small arithmetic helpers."""\n\n\n'
        "def add(a, b):\n"
        "    return a + b + 1  # BUG: extra off-by-one term\n\n\n"
        "def subtract(a, b):\n"
        "    return a - b\n",
    )
    _write(
        dest,
        "tests/test_mathutils.py",
        "from mathutils import add, subtract\n\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n\n\n"
        "def test_subtract():\n"
        "    assert subtract(5, 3) == 2\n",
    )
    _write(dest, "README.md", "# Toy arithmetic bug\n\nFix `add` so both tests pass.\n")

    return MaterializeResult(
        path=dest,
        config=FixtureConfig(
            category="simple_bugs",
            broken_file_paths=["mathutils.py"],
            broken_line_offsets=[12],
            database="none",
            http="none",
        ),
    )


def toy_import_bug(dest: Path) -> MaterializeResult:
    """A tiny package with a missing import causing a NameError at test time.

    Category: "import bug" - no infrastructure needed.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    _write(
        dest,
        "greeter.py",
        '"""Greeting helper."""\n\n\n'
        "def shout(name):\n"
        "    return name.upper() + '!'\n\n\n"
        "def timestamp_prefix(name):\n"
        "    # BUG: 'datetime' is used but never imported\n"
        "    return f'{datetime.datetime.now().isoformat()} {name}'\n",
    )
    _write(
        dest,
        "tests/test_greeter.py",
        "from greeter import shout, timestamp_prefix\n\n\n"
        "def test_shout():\n"
        "    assert shout('fly') == 'FLY!'\n\n\n"
        "def test_timestamp_prefix():\n"
        "    result = timestamp_prefix('fly')\n"
        "    assert result.endswith('fly')\n",
    )
    _write(dest, "README.md", "# Toy import bug\n\nFix import errors.\n")

    return MaterializeResult(
        path=dest,
        config=FixtureConfig(
            category="import_bugs",
            broken_file_paths=["greeter.py"],
            broken_line_offsets=[10],
            database="none",
            http="none",
        ),
    )


def toy_regression_bug(dest: Path) -> MaterializeResult:
    """Start clean, then edit a file to break tests.

    Category: "regression" - demonstrates the difference between
    'fixing bugs' and 'causing new bugs'.
    """
    import os

    # First, set up a working FastAPI + Postgres app
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    _write(dest, "pyproject.toml", "[tool.poetry]\nname = 'fastapi_app'\nversion = '0.1.0'")
    _write(dest, "docker-compose.yml", "version: '3.8'\nservices:\n  app:\n    build: .\n    ports:\n      - '8000:8000'\n  postgres:\n    image: postgres:15\n    environment:\n      - POSTGRES_USER=postgres\n      - POSTGRES_PASSWORD=secret\n      - POSTGRES_DB=appdb")

    _write(dest, "alembic.ini", "[loggers]\nkeys = main\n")
    _write(dest, "env.py", "from pathlib import Path\nfrom logging.config import fileConfig\nimport sys\n\nif __name__ == '__main__':\n    import sys as _sys\n    _sys.exit(0)\n")

    _write(dest, "main.py", '"""Application entry point."""\nfrom fastapi import FastAPI, Depends\nfrom sqlalchemy.orm import Session\n\napp = FastAPI(title="Toy App")\n\ndef get_db():\n    connection = yield from get_db_depends()\n    try:\n        yield connection\n    finally:\n        connection.close()\n\ndef get_db_depends():\n    connection = yield from get_db_engine()\n    return SessionLocal(\n        async_sessionmaker(\n            Session, autocommit=False, autoflush=False\n        )\n    )\n\napp = FastAPI(\n    title="Toy App",\n    version="0.1.0",\n    description="A toy FastAPI application with Postgres.",\n)\n\n@app.get("/")\nasync def root():\n    return {"message": "Hello World"}\n')
    _write(dest, "app.py", '"""Main application logic."""\ndef greet(name: str) -> str: return f"Hello, {name}!"')

    return MaterializeResult(
        path=dest,
        config=FixtureConfig(
            category="regressions",
            database="postgres",
            web_port=8000,
            migrations=[{"name": "001_init", "path": "migrations/versions/001_init.py"}],
        ),
    )


def toy_import_bug(dest: Path) -> Path:
    """A tiny package with a missing import causing a NameError at test time."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    _write(
        dest,
        "greeter.py",
        '"""Greeting helper."""\n\n\n'
        "def shout(name):\n"
        "    return name.upper() + '!'\n\n\n"
        "def timestamp_prefix(name):\n"
        "    # BUG: 'datetime' is used but never imported\n"
        "    return f'{datetime.datetime.now().isoformat()} {name}'\n",
    )
    _write(
        dest,
        "tests/test_greeter.py",
        "from greeter import shout, timestamp_prefix\n\n\n"
        "def test_shout():\n"
        "    assert shout('fly') == 'FLY!'\n\n\n"
        "def test_timestamp_prefix():\n"
        "    result = timestamp_prefix('fly')\n"
        "    assert result.endswith('fly')\n",
    )
    return dest


FIXTURES = {
    "toy_arithmetic_bug": toy_arithmetic_bug,
    "toy_import_bug": toy_import_bug,
}


def materialize(name: str, dest: Path) -> Path:
    """Build fixture `name` fresh into `dest` and return it."""
    try:
        builder = FIXTURES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown fixture '{name}'. Available: {sorted(FIXTURES)}") from exc
    return builder(dest)
