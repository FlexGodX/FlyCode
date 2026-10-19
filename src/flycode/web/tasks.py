"""Protected tracker HTTP views use the canonical store and host-issued scope."""
import asyncio
import hashlib
import json
import time
from contextlib import contextmanager
from threading import Lock
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import StreamingResponse
from pydantic import Field, ValidationError

from ..domain.orchestration import (
    AccessScope,
    QueueEvent,
    QueueModel,
    TaskCommand,
    TaskDraft,
    TaskState,
    WorkflowBudget,
    WorkflowDraft,
)
from ..domain.project import TaskStopPage, TaskStopReceipt
from ..orchestration.attach import (
    AttachmentStatus,
    CoordinatorClient,
    attach_coordinator,
)
from ..orchestration.projects import Projects
from ..orchestration.store import Store, StoreError, default_state_path
from ..tracker.commands import CommentCommand
from ..tracker.materials import MaterialPage
from ..tracker.projection import (
    AgentCard,
    TaskCard,
    TaskEventPage,
    TaskPage,
    TaskSnapshot,
)
from ..tracker.service import TrackerService
from .projects import project_options
from .verification import verification_router


class CoordinatorAttachmentRequest(QueueModel):
    pass


class ProjectRegistration(QueueModel):
    selector: str = Field(min_length=1, max_length=1000)


class TaskStop(QueueModel):
    operation: Literal['pause', 'cancel']
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class TaskCreation(QueueModel):
    project_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    task: TaskDraft
    idempotency_key: str = Field(min_length=1, max_length=200)
    workflow_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    workflow_budget: WorkflowBudget | None = None


def task_router(manager) -> APIRouter:
    router = APIRouter()
    coordinator_gate = Lock()
    actor = 'web:' + hashlib.sha256((str(manager.workspace) + '\0' + str(manager.data_dir)).encode()).hexdigest()

    def choices():
        with manager.lock:
            registry = manager.projects.model_copy(deep=True)
        return [item for item in project_options(manager.workspace, registry) if item['id'] != 'demo']

    def issued_scope(store, project_id=None):
        projects = set()
        for item in choices():
            try:
                projects.add(Projects(store).resolve(manager.project_path(item['id']), actor_id=actor).id)
            except (OSError, ValueError):
                continue
        scope = AccessScope(actor_id=actor, project_ids=projects, role='owner')
        if project_id is not None:
            store._scope(scope, project_id)
            scope = AccessScope(actor_id=actor, project_ids={project_id}, role='owner')
        return scope

    @contextmanager
    def access(project_id=None):
        store = Store(default_state_path())
        try:
            yield store, TrackerService(store), issued_scope(store, project_id)
        except StoreError as error:
            status = 403 if error.code in {'scope', 'permission'} else 404 if error.code == 'not_found' else 409
            detail = {'code': error.code}
            if error.current is not None:
                detail['current'] = error.current
            raise HTTPException(status, detail) from error
        except ValidationError as error:
            raise HTTPException(422, {'code': 'invalid_fields'}) from error
        finally:
            store.close()

    @router.get('/api/tasks/coordinator')
    def coordinator_status() -> AttachmentStatus:
        try:
            with CoordinatorClient(default_state_path()) as client:
                return client.status()
        except StoreError as error:
            raise HTTPException(409, {'code': error.code}) from error

    @router.post('/api/tasks/coordinator/attach')
    def coordinator_attach(body: CoordinatorAttachmentRequest) -> AttachmentStatus:
        if not coordinator_gate.acquire(blocking=False):
            raise HTTPException(409, {'code': 'attachment_starting'})
        try:
            with attach_coordinator(default_state_path()) as client:
                return client.status()
        except StoreError as error:
            raise HTTPException(409, {'code': error.code}) from error
        finally:
            coordinator_gate.release()

    @router.get('/api/tasks/stops')
    def stop_handles(after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
                     limit: Annotated[int, Query(ge=1, le=100)] = 50) -> TaskStopPage:
        store = Store(default_state_path())
        try:
            return Projects(store).stop_page(actor, after=after, limit=limit)
        except StoreError as error:
            raise HTTPException(403 if error.code == 'scope' else 409, {'code': error.code}) from error
        finally:
            store.close()

    @router.get('/api/tasks/projects')
    def projects() -> list[dict]:
        with access() as (store, _, _scope):
            result = []
            for item in choices():
                row = {'selector': item['id'], 'name': item['name'], 'project_id': None, 'status': 'registration_required'}
                try:
                    project = Projects(store).resolve(manager.project_path(item['id']), actor_id=actor)
                    row.update(project_id=project.id, status='ready')
                except StoreError as error:
                    if error.code != 'project_not_registered':
                        row['status'] = 'unavailable'
                except (OSError, ValueError):
                    row['status'] = 'unavailable'
                result.append(row)
            return result

    @router.post('/api/tasks/projects', status_code=201)
    def register_project(body: ProjectRegistration) -> dict:
        if body.selector not in {item['id'] for item in choices()}:
            raise HTTPException(403, {'code': 'scope'})
        with access() as (store, _, scope):
            try:
                root = manager.project_path(body.selector)
            except (OSError, ValueError) as error:
                raise HTTPException(409, {'code': 'project_unavailable'}) from error
            project = Projects(store).register(root, actor_id=scope.actor_id)
            return {'selector': body.selector, 'project_id': project.id}

    @router.get('/api/tasks')
    def list_tasks(after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                   project_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{32}$')] = None, state: TaskState | None = None,
                   q: Annotated[str, Query(max_length=200)] = '', archived: bool = False) -> TaskPage:
        with access(project_id) as (_, tracker, scope):
            return tracker.page(scope, after=after, limit=limit, project_id=project_id, state=state, q=q, archived=archived)

    @router.post('/api/tasks', status_code=201)
    def create_task(body: TaskCreation) -> TaskCard:
        with access(body.project_id) as (store, tracker, scope):
            if body.workflow_id is None:
                workflow = tracker.create(scope, WorkflowDraft(project_id=body.project_id, task=body.task,
                    budget=body.workflow_budget), key=body.idempotency_key)
                identity = workflow.root_task_id
            else:
                workflow = store.workflow(scope, body.workflow_id)
                if workflow.project_id != body.project_id or body.workflow_budget is not None:
                    raise StoreError('scope')
                identity = store.create_task(scope, workflow.id, body.task, key=body.idempotency_key).id
            return tracker.task(scope, identity)

    def snapshot(project_id):
        with access(project_id) as (_, tracker, scope):
            return tracker.page(scope, limit=100), scope.project_ids

    def pending(project_id, cursor):
        with access(project_id) as (_, tracker, scope):
            events, frontier = tracker.events(scope, after=cursor)
            return events, frontier, scope.project_ids

    def sse(kind, cursor, data):
        return f'id: {cursor}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False)}\n\n'

    @router.get('/api/tasks/events')
    def stream_events(request: Request, after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
                      project_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{32}$')] = None) -> StreamingResponse:
        try:
            header = int(request.headers.get('last-event-id', '0'))
            if not 0 <= header <= 2**63 - 1:
                raise ValueError('negative cursor')
            requested = max(header, after)
        except ValueError as error:
            raise HTTPException(422, {'code': 'cursor'}) from error
        initial, projects = snapshot(project_id)
        if requested > initial.cursor:
            raise HTTPException(422, {'code': 'cursor_future'})
        resume = 'last-event-id' in request.headers or 'after' in request.query_params

        async def journal():
            nonlocal projects
            cursor, heartbeat = requested if resume else initial.cursor, time.monotonic()
            scan = cursor
            page = TaskSnapshot(**initial.model_dump(), replay_after=cursor)
            yield sse('snapshot', cursor, page.model_dump(mode='json'))
            while not await request.is_disconnected():
                try:
                    events, frontier, current = await asyncio.to_thread(pending, project_id, scan)
                    if current != projects:
                        page, projects = await asyncio.to_thread(snapshot, project_id)
                        scan = cursor
                        page = TaskSnapshot(**page.model_dump(), replay_after=cursor)
                        yield sse('snapshot', cursor, page.model_dump(mode='json'))
                    else:
                        for event in events:
                            yield sse('change', event.sequence, event.model_dump(mode='json'))
                            cursor = event.sequence
                        scan = frontier
                except HTTPException as error:
                    yield sse('error', cursor, error.detail)
                    break
                if time.monotonic() - heartbeat >= 10:
                    yield ': heartbeat\n\n'
                    heartbeat = time.monotonic()
                await asyncio.sleep(.25)

        return StreamingResponse(journal(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @router.get('/api/agents/activity')
    def agents() -> list[AgentCard]:
        with access() as (_, tracker, scope):
            return tracker.agents(scope)

    @router.get('/api/tasks/{identity}')
    def task(identity: Annotated[str, PathParam(pattern=r'^[0-9a-f]{32}$')]) -> TaskCard:
        with access() as (_, tracker, scope):
            return tracker.task(scope, identity)

    @router.get('/api/tasks/{identity}/materials')
    def materials(identity: Annotated[str, PathParam(pattern=r'^[0-9a-f]{32}$')]) -> MaterialPage:
        with access() as (_, tracker, scope):
            return tracker.materials(scope, identity)

    @router.get('/api/tasks/{identity}/materials/{material_id}')
    def material(identity: Annotated[str, PathParam(pattern=r'^[0-9a-f]{32}$')],
                 material_id: Annotated[str, PathParam(pattern=r'^[0-9a-f]{64}$')]) -> Response:
        with access() as (_, tracker, scope):
            try:
                item, content = tracker.material(scope, identity, material_id)
            except (OSError, ValueError) as error:
                if isinstance(error, StoreError):
                    raise
                raise StoreError('material_changed') from error
            return Response(content, media_type='application/octet-stream', headers={
                'Content-Disposition': f'attachment; filename="{item.id}.bin"',
                'Content-Security-Policy': "sandbox; default-src 'none'",
                'X-Content-Type-Options': 'nosniff'})

    @router.get('/api/tasks/{identity}/events')
    def task_history(identity: Annotated[str, PathParam(pattern=r'^[0-9a-f]{32}$')],
                     after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
                     limit: Annotated[int, Query(ge=1, le=200)] = 50) -> TaskEventPage:
        with access() as (_, tracker, scope):
            return tracker.task_events(scope, identity, after=after, limit=limit)

    @router.post('/api/tasks/{identity}/commands')
    def command(identity: Annotated[str, PathParam(pattern=r'^[0-9a-f]{32}$')], body: TaskCommand) -> TaskCard:
        with access() as (_, tracker, scope):
            tracker.command(scope, identity, body.model_dump())
            return tracker.task(scope, identity)

    @router.post('/api/tasks/{identity}/stop')
    def stop(identity: Annotated[str, PathParam(pattern=r'^[0-9a-f]{32}$')], body: TaskStop) -> TaskStopReceipt:
        store = Store(default_state_path())
        try:
            task = Projects(store).stop_command(actor, identity, TaskCommand(**body.model_dump()))
            return TaskStopReceipt(task_id=task.id, revision=task.revision, state=task.state, paused=task.paused)
        except StoreError as error:
            detail = {'code': error.code}
            if error.code == 'revision' and error.current is not None:
                detail['revision'] = error.current['revision']
            raise HTTPException(403 if error.code in {'scope', 'permission'} else 409, detail) from error
        finally:
            store.close()

    @router.post('/api/tasks/{identity}/comments', status_code=201)
    def post_comment(identity: Annotated[str, PathParam(pattern=r'^[0-9a-f]{32}$')], body: CommentCommand) -> QueueEvent:
        with access() as (_, tracker, scope):
            return tracker.comment(scope, identity, body)

    router.include_router(verification_router(access, actor))
    return router
