"""Protected owner adapters resolve only folders selected in this application."""

import hashlib
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import JSONResponse

from ..orchestration.attach import CoordinatorClient
from ..orchestration.owner_requests import (
    OwnerCommand,
    OwnerMessageRoutesPreview,
    OwnerPreview,
)
from ..orchestration.projects import Projects
from ..orchestration.store import Store, StoreError, default_state_path
from .projects import project_options


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
            if not isinstance(command, (OwnerPreview, OwnerMessageRoutesPreview)):
                Projects(store)._grant(project, actor)
            with CoordinatorClient(store.path) as client:
                outcome = client.owner(command.model_dump(mode='json'))
            if outcome.error_code is not None:
                status = 403 if outcome.error_code in {'scope', 'permission'} else 409
                return JSONResponse(status_code=status, content={'detail': {'code': outcome.error_code,
                    'result': outcome.result.model_dump(mode='json') if outcome.result else None}})
            return outcome
        except StoreError as error:
            raise HTTPException(403 if error.code in {'scope', 'permission'} else 409, {'code': error.code}) from error
        finally:
            store.close()

    @router.post('/api/tasks/owner')
    def owner_command(command: OwnerCommand):
        return forward(command)

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
