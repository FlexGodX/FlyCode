"""Loopback-only HTTP API and packaged browser interface."""

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..llm.catalogue import ModelCatalogue
from .runs import ACTIVE, RunManager, RunRequest

STATIC = Path(__file__).with_name("static")


class ModelProbeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_ref: str = Field(min_length=1, max_length=100)
    role: Literal['primary', 'scorer']


def create_app(workspace: Path | None = None, data_dir: Path | None = None,
               artifact_root: Path | None = None) -> FastAPI:
    manager = RunManager(workspace or Path.cwd(), data_dir or Path.home() / ".local/share/flycode/ui", artifact_root)
    token = secrets.token_urlsafe(32)
    model_catalogue = ModelCatalogue()
    model_gate = Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        manager.close()

    app = FastAPI(title="FlyCode", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.runs = manager

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "Local host required"}, status_code=400)
        origin = request.headers.get("origin")
        if ((origin and urlparse(origin).netloc != request.url.netloc)
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"detail": "Foreign origin rejected"}, status_code=403)
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
            request.headers.get("x-flycode-token", ""), token,
        ):
            return JSONResponse({"detail": "Missing session token; reload FlyCode"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/api/bootstrap")
    def bootstrap() -> dict:
        projects = [{"id": "demo", "name": "Демо-проект", "description": "Отдельная папка для каждого запуска"},
                    {"id": ".", "name": manager.workspace.name, "description": str(manager.workspace)}]
        for path in sorted(manager.workspace.iterdir()):
            if path.name.startswith(".") or not path.is_dir() or not path.resolve().is_relative_to(manager.workspace):
                continue
            if (path / ".git").exists() or (path / "pyproject.toml").exists() or (path / "package.json").exists():
                projects.append({"id": f"./{path.name}", "name": path.name, "description": str(path)})
        with manager.lock:
            catalogue = manager.catalogue.refresh()
        return {"token": token, "workspace": str(manager.workspace), "projects": projects, **catalogue}

    @app.get("/api/models")
    def models() -> dict:
        try:
            entries = model_catalogue.discover()
            return {"connected": True, "models": [entry.model_id for entry in entries if entry.status != 'unavailable'],
                    "catalogue": [entry.model_dump() for entry in entries], "error": ""}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return {"connected": False, "models": [], "catalogue": [],
                    "error": "Не удалось подключиться к LM Studio. Запустите локальный сервер."}

    @app.post("/api/models/probe")
    def probe_model(body: ModelProbeRequest) -> dict:
        if not model_gate.acquire(blocking=False):
            raise HTTPException(409, "Другая проверка модели ещё выполняется")
        try:
            with manager.lock:
                if any(record.status in ACTIVE for record in manager.records.values()):
                    raise HTTPException(409, "Дождитесь завершения текущей задачи")
            try:
                return model_catalogue.probe(body.model_ref, body.role).model_dump()
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        finally:
            model_gate.release()

    @app.get("/api/runs")
    def history() -> list[dict]:
        return manager.history()

    def require_run(run_id: str) -> None:
        if run_id not in manager.records:
            raise HTTPException(404, "Run not found")

    @app.post("/api/runs", status_code=201)
    def start_run(body: RunRequest) -> dict:
        if not model_gate.acquire(blocking=False):
            raise HTTPException(409, "Дождитесь проверки модели")
        try:
            return manager.start(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            model_gate.release()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        require_run(run_id)
        return manager.snapshot(run_id)

    @app.post("/api/runs/{run_id}/stop")
    def stop_run(run_id: str) -> dict:
        require_run(run_id)
        return manager.stop(run_id)

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
        require_run(run_id)
        try:
            cursor = max(0, after, int(request.headers.get("last-event-id", "0")))
        except ValueError as exc:
            raise HTTPException(422, "Invalid event cursor") from exc

        async def journal():
            nonlocal cursor
            heartbeat = time.monotonic()
            while not await request.is_disconnected():
                with manager.lock:
                    pending = [event for event in manager.events[run_id] if event.id > cursor]
                    done = manager.records[run_id].status not in ACTIVE
                for event in pending:
                    yield f"id: {event.id}\ndata: {event.model_dump_json()}\n\n"
                    cursor = event.id
                if done:
                    break
                if time.monotonic() - heartbeat > 10:
                    yield ": heartbeat\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.1)

        return StreamingResponse(journal(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
