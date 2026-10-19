"""Owner-approved graph edges; never inferred from ancestry or model text."""

import json
from uuid import uuid4

from ..domain.message_routes import (
    MessageGrantPin,
    MessageRouteGrant,
    MessageTaskPin,
    ResolvedMessageRoutes,
)
from ..domain.orchestration import QueueEvent
from ..verification.registry import VerificationRegistry
from ..verification.snapshot import digest
from .integration_bindings import record_hash
from .owner_requests import OwnerMessageRoutesResult
from .pipeline import Pipelines
from .store import StoreError, covers


class MessageRoutes:
    def __init__(self, store):
        self.store = store

    def _pipeline(self, scope, identity):
        row = self.store.db.execute('SELECT data FROM pipelines WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'pipeline')
        recipe = Pipelines.effective(self.store, row['data'])
        workflow = self.store.workflow(scope, recipe.workflow_id)
        if recipe.project_id != workflow.project_id:
            raise StoreError('pipeline_scope')
        if self.store.host is None or recipe.runtime_root != self.store.host.registration.runtime_root.path:
            raise StoreError('pipeline_scope')
        return recipe

    def _head(self, workflow_id):
        row = self.store.db.execute("""SELECT
            (SELECT grant_id FROM message_route_heads WHERE workflow_id=?) AS grant_id,
            (SELECT json_extract(data,'$.data.grant_id') FROM events WHERE workflow_id=?
                AND json_extract(data,'$.kind')='message_routes_approved'
                ORDER BY sequence DESC LIMIT 1) AS event_id""", (workflow_id, workflow_id)).fetchone()
        if row['grant_id'] != row['event_id']:
            raise StoreError('message_route_changed')
        return row['grant_id']

    def _grant(self, scope, identity, workflow):
        row = self.store.db.execute('SELECT * FROM message_route_grants WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise StoreError('message_route_changed')
        try:
            grant = MessageRouteGrant.model_validate_json(row['data'])
        except ValueError as error:
            raise StoreError('message_route_changed') from error
        if (grant.id != identity or row['project_id'] != grant.project_id or row['workflow_id'] != grant.workflow_id
                or grant.project_id != workflow.project_id or grant.workflow_id != workflow.id):
            raise StoreError('message_route_changed')
        self.store._scope(scope, grant.project_id)
        events = self.store.db.execute("""SELECT sequence,project_id,workflow_id,data FROM events
            WHERE workflow_id=? AND json_extract(data,'$.kind')='message_routes_approved'
            AND json_extract(data,'$.data.grant_id')=?""", (workflow.id, identity)).fetchall()
        if len(events) != 1:
            raise StoreError('message_route_changed')
        try:
            event = QueueEvent.model_validate({**json.loads(events[0]['data']), 'sequence': events[0]['sequence']})
        except (ValueError, TypeError) as error:
            raise StoreError('message_route_changed') from error
        if (events[0]['project_id'] != grant.project_id or event.project_id != grant.project_id
                or event.workflow_id != grant.workflow_id or event.actor_id != grant.owner_id
                or event.data != {'grant_id': identity, 'previous_id': grant.previous_id,
                    'grant_hash': record_hash(grant), 'edges': [edge.model_dump() for edge in grant.edges]}):
            raise StoreError('message_route_changed')
        return grant

    def _edges(self, recipe, tasks, edges):
        for edge in edges:
            if edge.sender_task_id not in recipe.tasks or edge.recipient_task_id not in recipe.tasks:
                raise StoreError('message_route_scope')
            sender, recipient = tasks[edge.sender_task_id], tasks[edge.recipient_task_id]
            if (sender.permissions is None or not sender.permissions.approved
                    or 'send_message' not in sender.permissions.tools):
                raise StoreError('permission')
            profiles = [profile for profile in recipe.profiles.values()
                if not sender.allowed_profile_ids or profile.id in sender.allowed_profile_ids]
            if not any(profile.permissions.approved and 'send_message' in profile.permissions.tools
                    and recipe.project_id in profile.project_ids for profile in profiles):
                raise StoreError('permission')
            if recipient.permissions is None or not covers(recipient.permissions.read_scopes, sender.permissions.read_scopes):
                raise StoreError('message_scope')

    def approve(self, scope, project, command):
        store = self.store
        store._role(scope, ('owner',))
        store._scope(scope, project.id)
        request = {'message_routes_v1': command.model_dump(mode='json')}
        key = 'owner-routes/' + digest({'key': command.idempotency_key})
        store._host_guard()
        with store.transaction():
            store._host_sql()
            VerificationRegistry(store)._project_sql(project)
            found, saved, request_hash = store._replay(scope, key, request)
            if found:
                return MessageRouteGrant.model_validate(saved)
            recipe = self._pipeline(scope, command.pipeline_id)
            workflow = store.workflow(scope, recipe.workflow_id)
            if workflow.project_id != project.id:
                raise StoreError('scope')
            tasks = {task.id: task for task in store.tasks(scope, workflow.id)}
            if (workflow.revision != command.expected_revision
                    or {identity: task.revision for identity, task in tasks.items()} != command.task_revisions):
                raise StoreError('revision')
            previous = self._head(workflow.id)
            if previous != command.expected_grant_id:
                raise StoreError('message_route_revision')
            if command.edges:
                Pipelines.guard_metadata(store, scope, recipe)
                self._edges(recipe, tasks, command.edges)
            grant = MessageRouteGrant(id=uuid4().hex, owner_id=scope.actor_id, project_id=project.id,
                workflow_id=workflow.id, workflow_revision=workflow.revision,
                workflow_semantic_revision=workflow.semantic_revision, pipeline_id=recipe.id,
                graph_hash=record_hash(recipe), task_pins={identity: MessageTaskPin(
                    semantic_revision=task.semantic_revision, contract_hash=task.contract_hash)
                    for identity, task in recipe.tasks.items()}, task_revisions=command.task_revisions,
                edges=command.edges, previous_id=previous, created_at=store.clock())
            store.db.execute('INSERT INTO message_route_grants VALUES (?,?,?,?)',
                (grant.id, grant.project_id, grant.workflow_id, grant.model_dump_json()))
            store.db.execute('INSERT INTO message_route_heads VALUES (?,?) ON CONFLICT(workflow_id) '
                'DO UPDATE SET grant_id=excluded.grant_id', (workflow.id, grant.id))
            store._event(scope, workflow, 'message_routes_approved', grant_id=grant.id,
                previous_id=previous, grant_hash=record_hash(grant), edges=[edge.model_dump() for edge in grant.edges])
            store._remember(scope, key, project.id, request_hash, grant)
            return grant

    def _authority(self, scope, grant):
        recipe = self._pipeline(scope, grant.pipeline_id)
        if (record_hash(recipe) != grant.graph_hash
                or recipe.workflow_id != grant.workflow_id or recipe.project_id != grant.project_id
                or recipe.workflow_semantic_revision != grant.workflow_semantic_revision
                or {identity: MessageTaskPin(semantic_revision=task.semantic_revision, contract_hash=task.contract_hash)
                    for identity, task in recipe.tasks.items()} != grant.task_pins):
            raise StoreError('message_route_stale')
        _, tasks, _ = Pipelines.guard_metadata(self.store, scope, recipe)
        self._edges(recipe, tasks, grant.edges)
        return recipe

    def require(self, scope, attempt_id, fence, pin):
        sender, task, workflow = self.store._owned(scope, attempt_id, fence, running=True)
        identity = self._head(workflow.id)
        if identity != pin.id:
            raise StoreError('message_route_stale')
        grant = self._grant(scope, identity, workflow)
        if record_hash(grant) != pin.sha256:
            raise StoreError('message_route_stale')
        self._authority(scope, grant)
        if (workflow.state != 'running' or task.paused or self.store._counter('queue_paused')
                or task.permissions is None or not task.permissions.approved
                or 'send_message' not in task.permissions.tools
                or not sender.profile_snapshot.permissions.approved
                or 'send_message' not in sender.profile_snapshot.permissions.tools
                or not any(edge.sender_task_id == task.id for edge in grant.edges)):
            raise StoreError('message_route_stale')
        return grant

    def live(self, scope, attempt_id, fence):
        sender, task, workflow = self.store._owned(scope, attempt_id, fence, running=True)
        identity = self._head(workflow.id)
        if identity is None:
            return ResolvedMessageRoutes()
        grant = self._grant(scope, identity, workflow)
        pin = MessageGrantPin(id=grant.id, sha256=record_hash(grant))
        if not any(edge.sender_task_id == task.id for edge in grant.edges):
            return ResolvedMessageRoutes()
        self.require(scope, attempt_id, fence, pin)
        recipients = []
        for edge in grant.edges:
            if edge.sender_task_id != sender.task_id:
                continue
            target = self.store.task(scope, edge.recipient_task_id)
            if target.current_attempt_id is None:
                continue
            attempt = self.store._attempt(target.current_attempt_id)
            if attempt.state not in {'assigned', 'running'}:
                continue
            recipients.append(attempt.instance_id)
        return ResolvedMessageRoutes(grant=pin, recipients=tuple(recipients))

    def result(self, project, grant):
        return OwnerMessageRoutesResult(project_id=project.id, project_revision=project.revision,
            workflow_id=grant.workflow_id, workflow_revision=grant.workflow_revision, pipeline_id=grant.pipeline_id,
            grant_id=grant.id, edges=grant.edges, active=bool(grant.edges))

    def view(self, scope, project, workflow_id):
        workflow = self.store.workflow(scope, workflow_id)
        if workflow.project_id != project.id:
            raise StoreError('scope')
        identity = self._head(workflow_id)
        if identity is None:
            return OwnerMessageRoutesResult(project_id=project.id, project_revision=project.revision,
                workflow_id=workflow_id, workflow_revision=workflow.revision, pipeline_id=None, grant_id=None)
        grant = self._grant(scope, identity, workflow)
        result = self.result(project, grant)
        try:
            if grant.edges:
                self._authority(scope, grant)
        except StoreError as error:
            return result.model_copy(update={'active': False, 'reason': error.code, 'workflow_revision': workflow.revision})
        return result.model_copy(update={'workflow_revision': workflow.revision})
