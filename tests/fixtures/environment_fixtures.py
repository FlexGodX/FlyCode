"""Test fixtures for software gym environments."""

import pytest
from pathlib import Path
import tempfile
import shutil
import os

from flycode.environment import TaskSpec, Observation


@pytest.fixture
def temp_repo(tmp_path):
    """Create temporary repository."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    
    # Create structure
    (repo / "README.md").write_text("# Test Project\n")
    (repo / "main.py").write_text("def main():\n    pass\n")
    (repo / "app.py").write_text("from flask import Flask\n\napp = Flask(__name__)\n")
    (repo / "config.py").write_text("DEBUG = True\n")
    
    yield str(repo)
    
    # Cleanup
    shutil.rmtree(repo)


@pytest.fixture
def broken_fastapi_repo(tmp_path):
    """Create broken FastAPI repo with common issues."""
    repo = tmp_path / "broken_fastapi"
    repo.mkdir()
    
    # Create broken FastAPI app
    (repo / "README.md").write_text("""# FastAPI Application
BROKEN VERSION

## Requirements
- Python >= 3.10
- FastAPI
- PostgreSQL
- uvicorn
""")
    
    (repo / "requirements.txt").write_text("""fastapi==0.104.1
uvicorn==0.24.0
sqlalchemy==2.0.0
pytest==7.4.0
psycopg2-binary==2.9.9
""")
    
    (repo / ".docker-compose.yml").write_text("""version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:password@db:5432/app
    depends:
      - db
    
  db:
    image: postgres:15
    environment:
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=password
      - POSTGRES_DB=app
    ports:
      - "5432:5432"
""")
    
    (repo / "Dockerfile").write_text("""FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
""")
    
    (repo / "main.py").write_text("""from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import psycopg2

app = FastAPI(title="Test API")

# TODO: Fix these issues
# - Missing database connection
# - Undefined variable database
# - Missing @app.on_event("startup")
# - Missing lifespan manager

class User(BaseModel):
    id: int
    name: str
    email: str

@app.get("/users")
def get_users():
    # BUG: database is undefined
    conn = database.connect()
    return {"message": "OK"}

@app.post("/users")
def create_user(user: User):
    # BUG: database connection
    return {"message": "Created"}
""")
    
    (repo / "database.py").write_text("""import psycopg2

# BUG: Wrong connection parameters
def get_connection():
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        username="wrong_user",
        password="wrong_password",
        database="wrong_db"
    )
    return conn
""")
    
    (repo / "tests").mkdir()
    (repo / "tests" / "test_users.py").write_text("""import pytest
import os

@pytest.fixture
def client(client):
    # BUG: Missing client fixture
    pass

def test_get_users(client):
    # BUG: client is not defined
    response = client.get("/users")
    assert response.status_code == 200
""")
    
    (repo / "tests" / "test_db.py").write_text("""import psycopg2

def test_connection():
    # BUG: Wrong connection string
    conn = psycopg2.connect("postgresql://user:pass@localhost/missing_db")
    assert conn is not None
""")
    
    yield str(repo)
    
    # Cleanup
    shutil.rmtree(repo)


@pytest.fixture
def task_fixtures():
    """Task specifications for testing."""
    return [
        {
            "name": "fix_fastapi_db",
            "task": "Fix this FastAPI application. It should connect to PostgreSQL and serve a /users endpoint. Make sure the app starts with docker-compose and all tests pass.",
            "expected_outcome": "Working FastAPI app with PostgreSQL",
            "expected_steps": [
                "read_file main.py",
                "run_build",
                "run_tests",
                "fix db connection",
                "run_tests",
            ],
        },
        {
            "name": "fix_python_bug",
            "task": "Fix this Python script. It should read a file and count lines.",
            "expected_outcome": "Working line counter script",
            "expected_steps": ["fix syntax error", "run_tests"],
        },
    ]