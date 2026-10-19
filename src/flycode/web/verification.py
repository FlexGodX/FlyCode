"""Protected server-ID verification commands, projections and report downloads."""

import asyncio
import json
import time
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import StreamingResponse

from ..domain.verification_control import VerificationCommand, VerificationPlanDraft
from ..orchestration.store import Store, StoreError, default_state_path
from ..verification.control import VerificationControl
from ..verification.projection import (
    PlanPage,
    PlanView,
    StopPage,
    VerificationViews,
    command_receipt,
)
from ..verification.registry import VerificationRegistry

Identity = Annotated[str, PathParam(pattern=r'^[0-9a-f]{32}$')]
Cursor = Annotated[int, Query(ge=0, le=2**63 - 1)]


def failure(error):
    status = 403 if error.code in {'scope', 'permission'} else 404 if error.code == 'not_found' else 409
    detail = {'code': error.code}
    if error.code == 'revision' and error.current is not None:
        detail['revision'] = error.current['revision']
    return HTTPException(status, detail)


def verification_router(access, actor):
    router = APIRouter(prefix='/api/verification')

    @contextmanager
    def authorized(project_id=None):
        with access(project_id) as (store, _, scope):
            try:
                yield store, scope
            except StoreError as error:
                raise failure(error) from error
            except (OSError, ValueError) as error:
                raise HTTPException(409, {'code': 'verification_unavailable'}) from error

    @router.get('/stops')
    def stops(after: Cursor = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> StopPage:
        store = Store(default_state_path())
        try:
            return VerificationViews(store).stops(actor, after=after, limit=limit)
        except StoreError as error:
            raise failure(error) from error
        finally:
            store.close()

    @router.get('/catalogue')
    def catalogue(project_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{32}$')] = None) -> dict:
        subjects, profiles = [], []
        with authorized(project_id) as (store, scope):
            registry = VerificationRegistry(store)
            for project in sorted(scope.project_ids):
                for row in store.db.execute('SELECT id FROM verification_subjects WHERE project_id=? ORDER BY rowid', (project,)).fetchall():
                    try:
                        subject = registry.subject(scope, row['id'])
                        registry.subject_root(scope, subject.id)
                        subjects.append({'id': subject.id, 'project_id': project,
                            'subject_hash': subject.snapshot.subject_hash,
                            'origin_kind': subject.origin.kind if subject.origin else 'project',
                            'workflow_id': subject.snapshot.ref.workflow_id if subject.snapshot.ref else None,
                            'task_id': subject.snapshot.ref.task_id if subject.snapshot.ref else None,
                            'stage': subject.origin.stage if subject.origin else 'target'})
                    except (OSError, ValueError):
                        continue
                for row in store.db.execute('SELECT id FROM verification_profiles WHERE project_id=? ORDER BY rowid', (project,)).fetchall():
                    try:
                        profile = registry.profile(scope, row['id'])
                        profiles.append({'id': profile.id, 'project_id': project, 'version': profile.version,
                            'title': profile.draft.title, 'checks': [check.model_dump(mode='json') for check in profile.draft.checks],
                            'reruns': profile.draft.reruns,
                            'acceptance': profile.draft.acceptance.model_dump(mode='json') if profile.draft.acceptance else None,
                            'scope': 'acceptance-contract' if profile.draft.acceptance else 'registered-suite'})
                    except (OSError, ValueError):
                        continue
        return {'subjects': subjects, 'profiles': profiles}

    @router.get('/plans')
    def plans(after: Cursor = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50,
              project_id: Annotated[str | None, Query(pattern=r'^[0-9a-f]{32}$')] = None) -> PlanPage:
        with authorized(project_id) as (store, scope):
            return VerificationViews(store).page(scope, after=after, limit=limit)

    @router.post('/plans', status_code=201)
    def create(body: VerificationPlanDraft) -> PlanView:
        with authorized() as (store, scope):
            record = VerificationRegistry(store).create_plan(scope, body)
            return VerificationViews(store).plan(scope, record.id)

    @router.get('/plans/{identity}')
    def plan(identity: Identity) -> PlanView:
        with authorized() as (store, scope):
            return VerificationViews(store).plan(scope, identity)

    @router.post('/plans/{identity}/commands')
    def command(identity: Identity, body: VerificationCommand) -> dict:
        if body.operation == 'cancel':
            store = Store(default_state_path())
            try:
                return command_receipt(VerificationViews(store).cancel(actor, identity, body)).model_dump(mode='json')
            except StoreError as error:
                raise failure(error) from error
            finally:
                store.close()
        with authorized() as (store, scope):
            return command_receipt(VerificationControl(store).command(scope, identity, body)).model_dump(mode='json')

    @router.get('/plans/{identity}/artifacts/{artifact_id}')
    def artifact(identity: Identity, artifact_id: Annotated[str, PathParam(pattern=r'^[0-9a-f]{64}$')]) -> Response:
        with authorized() as (store, scope):
            item, content = VerificationViews(store).artifact(scope, identity, artifact_id)
            return Response(content, media_type='application/octet-stream', headers={
                'Content-Disposition': f'attachment; filename="{item.name.rsplit("/", 1)[-1]}"',
                'Content-Security-Policy': "sandbox; default-src 'none'"})

    @router.get('/plans/{identity}/events')
    def events(identity: Identity, request: Request, after: Cursor = 0) -> StreamingResponse:
        with authorized() as (store, scope):
            initial = VerificationViews(store).plan(scope, identity)
        try:
            header = int(request.headers.get('last-event-id', '0'))
            requested = max(header, after)
            if not 0 <= header <= 2**63 - 1 or requested > initial.cursor:
                raise ValueError('cursor')
        except ValueError as error:
            raise HTTPException(422, {'code': 'cursor'}) from error
        resume = 'last-event-id' in request.headers or 'after' in request.query_params

        def pending(cursor):
            with authorized() as (store, scope):
                return VerificationControl(store).events(scope, identity, after=cursor)

        def sse(kind, cursor, data):
            return f'id: {cursor}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False)}\n\n'

        async def journal():
            cursor = requested if resume else initial.cursor
            yield sse('snapshot', cursor, {**initial.model_dump(mode='json'), 'replay_after': cursor})
            heartbeat = time.monotonic()
            while not await request.is_disconnected():
                try:
                    changes = await asyncio.to_thread(pending, cursor)
                except HTTPException as error:
                    yield sse('error', cursor, error.detail)
                    break
                for change in changes:
                    cursor = change.sequence
                    yield sse('change', cursor, change.model_dump(mode='json'))
                if time.monotonic() - heartbeat >= 10:
                    yield ': heartbeat\n\n'
                    heartbeat = time.monotonic()
                await asyncio.sleep(.25)

        return StreamingResponse(journal(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    return router
