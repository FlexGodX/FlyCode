"""Protected owner adapters resolve only folders selected in this application."""

import hashlib
import json
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..domain.agent_requests import AgentRequestContext, AgentTaskRequest
from ..orchestration.attach import CoordinatorClient
from ..orchestration.owner_requests import (
    AgentCredentialCommand,
    AgentRequestStatus,
    AgentRequestTask,
    CommandKey,
    OwnerBase,
    OwnerIssueAgentContext,
    OwnerMessageRoutesPreview,
    OwnerPreview,
    OwnerTaskRequestResult,
    Revision,
    owner_request,
)
from ..orchestration.projects import Projects
from ..orchestration.store import Store, StoreError, default_state_path
from .projects import project_options


class AgentContextBody(OwnerBase):
    expected_instance_revision: Revision
    idempotency_key: CommandKey


def owner_router(manager):
    router = APIRouter()
    actor = 'web:' + hashlib.sha256((str(manager.workspace) + '\0' + str(manager.data_dir)).encode()).hexdigest()

    def allowed(store, identity):
        with manager.lock:
            registry = manager.projects.model_copy(deep=True)
        for item in project_options(manager.workspace, registry):
            if item['id'] == 'demo':
                continue
            try:
                project = Projects(store).resolve(manager.project_path(item['id']), actor_id=None)
                if project.id == identity:
                    return project
            except (OSError, ValueError):
                continue
        raise StoreError('scope')

    def forward(command):
        store = Store(default_state_path())
        try:
            project = allowed(store, command.project_id)
            if project.revision != command.project_revision:
                raise StoreError('project_changed')
            if not isinstance(command, (OwnerPreview, OwnerMessageRoutesPreview, AgentCredentialCommand)):
                Projects(store)._grant(project, actor)
            with CoordinatorClient(store.path) as client:
                outcome = client.owner(command.model_dump(mode='json'))
            if outcome.error_code is not None:
                status = 403 if outcome.error_code in {'scope', 'permission', 'agent_identity'} else 409
                return JSONResponse(status_code=status, content={'detail': {'code': outcome.error_code,
                    'result': outcome.result.model_dump(mode='json') if outcome.result else None}})
            return outcome
        except StoreError as error:
            raise HTTPException(403 if error.code in {'scope', 'permission'} else 409, {'code': error.code}) from error
        finally:
            store.close()

    @router.post('/api/agents/{instance_id}/task-request-context')
    def issue_context(instance_id: Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')], body: AgentContextBody):
        return forward(OwnerIssueAgentContext(operation='issue_agent_context', instance_id=instance_id,
            **body.model_dump()))

    def agent_command(request, instance_id, body=None, request_id=None):
        credentials = request.headers.getlist('x-flycode-agent-credential')
        if (len(credentials) != 1 or len(credentials[0]) != 97
                or re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{64}', credentials[0]) is None):
            raise HTTPException(403, {'code': 'agent_identity'})
        credential = credentials[0]
        store = Store(default_state_path())
        try:
            context = store._load('agent_request_contexts', credential[:32], AgentRequestContext)
            if context.instance_id != instance_id:
                raise StoreError('agent_identity')
            project = allowed(store, context.project_id)
        except (OSError, ValueError) as error:
            raise HTTPException(403, {'code': 'agent_identity'}) from error
        finally:
            store.close()
        fields = {'project_id': project.id, 'project_revision': project.revision,
            'instance_id': instance_id, 'credential': credential}
        command = (AgentRequestTask(operation='request_task', request=body, **fields) if body is not None else
            AgentRequestStatus(operation='task_request_status', request_id=request_id, **fields))
        outcome = forward(command)
        if isinstance(command, AgentRequestTask) and not isinstance(outcome, JSONResponse):
            if not isinstance(outcome.result, OwnerTaskRequestResult):
                raise HTTPException(409, {'code': 'agent_request_changed'})
            status = 202 if outcome.result.receipt.state == 'queued' else 200
            return JSONResponse(status_code=status, content=outcome.model_dump(mode='json'))
        return outcome

    @router.post('/api/agents/{instance_id}/task-requests')
    async def request_task(request: Request, instance_id: Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]):
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 128 * 1024:
                raise HTTPException(413, {'code': 'request_size'})
            raw.extend(chunk)
        try:
            body = AgentTaskRequest.model_validate(json.loads(raw))
        except (ValueError, RecursionError) as error:
            raise HTTPException(422, {'code': 'invalid_fields'}) from error
        return await run_in_threadpool(agent_command, request, instance_id, body=body)

    @router.get('/api/agents/{instance_id}/task-requests/{request_id}')
    def request_status(request: Request, instance_id: Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')],
                       request_id: Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')]):
        return agent_command(request, instance_id, request_id=request_id)

    @router.post('/api/tasks/owner')
    async def owner_command(request: Request):
        try:
            command = owner_request(await request.json())
        except (ValueError, RecursionError) as error:
            raise HTTPException(422, {'code': 'invalid_fields'}) from error
        return await run_in_threadpool(forward, command)

    @router.get('/api/tasks/owner/{workflow_id}')
    def owner_preview(workflow_id: Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')],
                      project_id: Annotated[str, Query(pattern=r'^[0-9a-f]{32}$')]):
        store = Store(default_state_path())
        try:
            project = allowed(store, project_id)
        except StoreError as error:
            raise HTTPException(403 if error.code == 'scope' else 409, {'code': error.code}) from error
        finally:
            store.close()
        return forward(OwnerPreview(operation='preview', project_id=project.id,
            project_revision=project.revision, workflow_id=workflow_id))

    @router.get('/api/tasks/owner/{workflow_id}/message-routes')
    def message_routes_preview(workflow_id: Annotated[str, Path(pattern=r'^[0-9a-f]{32}$')],
                               project_id: Annotated[str, Query(pattern=r'^[0-9a-f]{32}$')]):
        store = Store(default_state_path())
        try:
            project = allowed(store, project_id)
        except StoreError as error:
            raise HTTPException(403 if error.code == 'scope' else 409, {'code': error.code}) from error
        finally:
            store.close()
        return forward(OwnerMessageRoutesPreview(operation='message_routes_preview', project_id=project.id,
            project_revision=project.revision, workflow_id=workflow_id))

    return router
