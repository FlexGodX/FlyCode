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


def toy_import_bug(dest: Path) -> MaterializeResult:
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


_FASTAPI_POSTGRES_APP_FILES: dict[str, str] = {
    "Dockerfile": (
        "FROM python:3.12-slim\n"
        "\n"
        "WORKDIR /app\n"
        "\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
        "\n"
        "COPY . .\n"
        "\n"
        'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
    ),
    "docker-compose.yml": (
        "services:\n"
        "  db:\n"
        "    image: postgres:15\n"
        "    environment:\n"
        "      POSTGRES_USER: postgres\n"
        "      POSTGRES_PASSWORD: secret\n"
        "      POSTGRES_DB: appdb\n"
        "    healthcheck:\n"
        '      test: ["CMD-SHELL", "pg_isready -U postgres"]\n'
        "      interval: 2s\n"
        "      timeout: 2s\n"
        "      retries: 15\n"
        "\n"
        "  app:\n"
        "    build: .\n"
        "    environment:\n"
        "      DB_HOST: db\n"
        "      POSTGRES_PASSWORD: secret\n"
        "      DATABASE_NAME: appdb\n"
        "    ports:\n"
        "      # BUG: the app is published on 8001, but the README, the compose\n"
        "      # health check and every client expect http://localhost:8000\n"
        '      - "8001:8000"\n'
        "    depends_on:\n"
        "      db:\n"
        "        condition: service_healthy\n"
        "    healthcheck:\n"
        "      test:\n"
        "        [\"CMD\", \"python\", \"-c\",\n"
        "         \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\"]\n"
        "      interval: 2s\n"
        "      timeout: 3s\n"
        "      retries: 20\n"
    ),
    "requirements.txt": "alembic\nfastapi\nhttpx\npsycopg2-binary\npytest\nsqlalchemy\nuvicorn\n",
    "alembic.ini": (
        "[alembic]\n"
        "# BUG: the migration scripts live in migrations/, not alembic/\n"
        "script_location = alembic\n"
    ),
    "migrations/env.py": (
        "import os\n"
        "\n"
        "from alembic import context\n"
        "from sqlalchemy import engine_from_config, pool\n"
        "\n"
        "config = context.config\n"
        "\n"
        "# BUG: reads POSTGRES_PASS, which nothing sets - compose provides\n"
        "# POSTGRES_PASSWORD\n"
        'DB_PASSWORD = os.environ.get("POSTGRES_PASS", "postgres")\n'
        'DB_HOST = os.environ.get("DB_HOST", "localhost")\n'
        'DB_NAME = os.environ.get("DATABASE_NAME", "appdb")\n'
        "config.set_main_option(\n"
        '    "sqlalchemy.url",\n'
        '    f"postgresql+psycopg2://postgres:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}",\n'
        ")\n"
        "\n"
        "target_metadata = None\n"
        "\n"
        "\n"
        "def run_migrations_online() -> None:\n"
        "    connectable = engine_from_config(\n"
        '        config.get_section(config.config_ini_section, {}),\n'
        '        prefix="sqlalchemy.",\n'
        "        poolclass=pool.NullPool,\n"
        "    )\n"
        "    with connectable.connect() as connection:\n"
        "        context.configure(connection=connection, target_metadata=target_metadata)\n"
        "        with context.begin_transaction():\n"
        "            context.run_migrations()\n"
        "\n"
        "\n"
        "run_migrations_online()\n"
    ),
    "migrations/versions/001_create_users.py": (
        '"""create users table"""\n'
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "\n"
        'revision = "001"\n'
        "down_revision = None\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        "    op.create_table(\n"
        '        "users",\n'
        '        sa.Column("id", sa.Integer(), primary_key=True),\n'
        '        sa.Column("email", sa.String(length=255), nullable=False, unique=True),\n'
        "    )\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        '    op.drop_table("users")\n'
    ),
    "app/__init__.py": '"""Demo application package."""\n',
    "app/models.py": (
        "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n"
        "\n"
        "\n"
        "class Base(DeclarativeBase):\n"
        "    pass\n"
        "\n"
        "\n"
        "class User(Base):\n"
        '    __tablename__ = "users"\n'
        "\n"
        "    id: Mapped[int] = mapped_column(primary_key=True)\n"
        "    email: Mapped[str] = mapped_column(unique=True)\n"
    ),
    "app/database.py": (
        "import os\n"
        "\n"
        "from sqlalchemy import create_engine\n"
        "from sqlalchemy.orm import sessionmaker\n"
        "\n"
        "# BUG: compose and .env both provide POSTGRES_PASSWORD; nothing sets\n"
        "# POSTGRES_PASS\n"
        'DB_PASSWORD = os.environ.get("POSTGRES_PASS", "postgres")\n'
        'DB_HOST = os.environ.get("DB_HOST", "localhost")\n'
        'DB_NAME = os.environ.get("DATABASE_NAME", "appdb")\n'
        "\n"
        'DATABASE_URL = f"postgresql+psycopg2://postgres:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"\n'
        "\n"
        "engine = create_engine(DATABASE_URL, future=True)\n"
        "SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)\n"
        "\n"
        "\n"
        "def get_db():\n"
        "    db = SessionLocal()\n"
        "    try:\n"
        "        yield db\n"
        "    finally:\n"
        "        db.close()\n"
    ),
    "app/main.py": (
        '"""FastAPI application for the Phase 4 target demo (plan §51)."""\n'
        "from fastapi import Depends, FastAPI\n"
        "from sqlalchemy.orm import Session\n"
        "\n"
        "from app.database import get_db\n"
        "from app.models import User\n"
        "\n"
        'app = FastAPI(title="FlyCode Demo App")\n'
        "\n"
        "\n"
        '@app.get("/health")\n'
        "def health() -> dict:\n"
        '    return {"status": "ok"}\n'
        "\n"
        "\n"
        "# BUG: the documented endpoint is /users, not /user\n"
        '@app.get("/user")\n'
        "def list_users(db: Session = Depends(get_db)) -> list[dict]:\n"
        "    # BUG: sqlalchemy.select is never imported\n"
        "    rows = db.execute(select(User)).scalars().all()\n"
        '    return [{"id": row.id, "email": row.email} for row in rows]\n'
    ),
    "tests/__init__.py": "",
    "tests/test_users.py": (
        "from fastapi.testclient import TestClient\n"
        "from sqlalchemy import select\n"
        "\n"
        "from app.database import SessionLocal, engine\n"
        "from app.main import app\n"
        "from app.models import Base, User\n"
        "\n"
        "client = TestClient(app)\n"
        "\n"
        "\n"
        "def test_health():\n"
        '    assert client.get("/health").json() == {"status": "ok"}\n'
        "\n"
        "\n"
        "def test_list_users_returns_seeded_user():\n"
        "    Base.metadata.create_all(engine)\n"
        "    with SessionLocal() as session:\n"
        "        if not session.scalars(select(User)).first():\n"
        '            session.add(User(email="fly@example.com"))\n'
        "            session.commit()\n"
        "\n"
        '    response = client.get("/users")\n'
        "\n"
        "    assert response.status_code == 200\n"
        '    assert response.json() == [{"id": 1, "email": "fly@example.com"}]\n'
    ),
    "README.md": (
        "# FlyCode Phase 4 demo app\n"
        "\n"
        "FastAPI + PostgreSQL + SQLAlchemy + Alembic, started with Docker Compose.\n"
        "\n"
        "```bash\n"
        "docker compose up -d\n"
        "docker compose exec -T app python -m alembic upgrade head\n"
        "docker compose exec -T app python -m pytest -q\n"
        "curl http://localhost:8000/health\n"
        "curl http://localhost:8000/users\n"
        "```\n"
        "\n"
        "The application is intentionally broken. Fix the configuration, the\n"
        "migration and the code until `docker compose up` is healthy, the\n"
        "migration applies and the test suite passes.\n"
    ),
}


def fastapi_postgres_app(dest: Path) -> MaterializeResult:
    """The Phase 4 target demo: FastAPI + Postgres + Alembic + Docker Compose.

    Plan §51: intentionally broken in five places - docker configuration,
    migration, DB connection, API route and a Python bug - so that "get this
    application fully running" requires Docker, Postgres, Alembic, HTTP and
    service-health tool calls instead of a single file edit.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    for relative, content in _FASTAPI_POSTGRES_APP_FILES.items():
        _write(dest, relative, content)

    return MaterializeResult(
        path=dest,
        config=FixtureConfig(
            category="docker",
            broken_file_paths=[
                "docker-compose.yml",
                "alembic.ini",
                "app/database.py",
                "app/main.py",
            ],
            docker="default",
            docker_compose_file="docker-compose.yml",
            database="postgres",
            database_url="postgresql+psycopg2://postgres:secret@localhost:5432/appdb",
            migration_path="migrations/versions",
            http="custom",
            api_endpoint="http://localhost:8000/health",
            web_port=8000,
        ),
        docker_services=["db", "app"],
        database_type="postgres",
        api_endpoint="http://localhost:8000/users",
        health_check_url="http://localhost:8000/health",
    )


def search_helper_bug(dest: Path) -> MaterializeResult:
    """Held-out package task: reuse an existing monetary conversion helper."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    _write(dest, "billing/__init__.py", "")
    _write(dest, "billing/money.py", "def cents_to_amount(cents):\n    return round(cents / 100, 2)\n")
    _write(dest, "billing/service.py", "from . import money\n\ndef invoice_total(items):\n    return sum(items)\n")
    _write(dest, "tests/test_invoice.py",
           "from billing.service import invoice_total\n\n"
           "def test_invoice_uses_currency_units():\n    assert invoice_total([125, 275]) == 4.0\n\n"
           "def test_empty_invoice():\n    assert invoice_total([]) == 0.0\n\n"
           "def test_credit_note():\n    assert invoice_total([-75, 25]) == -0.5\n\n"
           "def test_shared_conversion_policy(monkeypatch):\n    from billing import money\n"
           "    monkeypatch.setattr(money, 'cents_to_amount', lambda cents: 7.5)\n"
           "    assert invoice_total([125, 275]) == 7.5\n")
    _write(dest, "README.md", "# Invoice totals\n\nItems are integer cents; invoice_total must return currency units. "
           "Find and reuse billing.money.cents_to_amount at call time so updates to the shared conversion policy "
           "also affect invoice totals. Preserve empty and credit-note behavior.\n")
    return MaterializeResult(dest, FixtureConfig(category=FixtureCategory.NOVEL_REPO,
                                                broken_file_paths=["billing/service.py"]))


def boundary_regression_bug(dest: Path) -> MaterializeResult:
    """Held-out boundary task with ordinary and invalid-input regressions."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    _write(dest, "shipping.py", 'def shipping_cost(total):\n    if total < 0:\n'
           '        raise ValueError("negative total")\n    return 0 if total > 50 else 5\n')
    _write(dest, "tests/test_shipping.py", "import pytest\nfrom shipping import shipping_cost\n\n"
           "@pytest.mark.parametrize(('total', 'cost'), [(0, 5), (49.99, 5), (50, 0), (50.01, 0), (100, 0)])\n"
           "def test_shipping_bands(total, cost):\n    assert shipping_cost(total) == cost\n\n"
           "def test_negative_total_is_rejected():\n    with pytest.raises(ValueError):\n        shipping_cost(-1)\n")
    _write(dest, "README.md", "# Shipping boundary\n\nShipping costs 5 below a total of 50, and is free "
           "at 50 or above. Negative totals must still raise ValueError. Fix the boundary without regressions.\n")
    return MaterializeResult(dest, FixtureConfig(category=FixtureCategory.REGRESSION,
                                                broken_file_paths=["shipping.py"]))


FIXTURES = {
    "toy_arithmetic_bug": toy_arithmetic_bug,
    "toy_import_bug": toy_import_bug,
    "fastapi_postgres_app": fastapi_postgres_app,
    "search_helper_bug": search_helper_bug,
    "boundary_regression_bug": boundary_regression_bug,
}


def materialize(name: str, dest: Path) -> MaterializeResult:
    """Build fixture `name` fresh into `dest` and return it."""
    try:
        builder = FIXTURES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown fixture '{name}'. Available: {sorted(FIXTURES)}") from exc
    return builder(dest)
