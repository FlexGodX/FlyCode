"""Scoped idle-instance credentials owned by one physical Host session."""

import hashlib
import hmac
import time
from pathlib import Path
from uuid import uuid4

from ..domain.agent_requests import (
    AgentContextIssue,
    AgentRequestContext,
    AgentRequestCredential,
)
from ..verification.registry import VerificationRegistry
from ..verification.snapshot import digest
from .projects import Projects
from .store import StoreError, changed


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class AgentRequests:
    def __init__(self, store):
        if store.host is None:
            raise StoreError('host_required')
        self.store = store

    def _credential(self, context):
        material = digest(context.model_dump(mode='json', exclude={'credential_hash'}))
        proof = hmac.new(self.store.host._agent_request_secret, material.encode(), hashlib.sha256).hexdigest()
        return context.id + '.' + proof

    def _same_host(self, context):
        host, now = self.store.host, self.store.clock()
        matching = ((context.host_registration_id, context.host_session_id, context.host_generation)
            == (host.registration.id, host.session.id, host.session.generation))
        live = (context.created_at <= now < context.expires_at
            and time.monotonic() < host._agent_request_deadlines.get(context.id, 0.))
        if matching and not live:
            host._agent_request_deadlines[context.id] = 0.
        return matching and live

    def issue(self, scope, project, *, instance_id, expected_revision, key) -> AgentRequestCredential:
        store = self.store
        store._role(scope, ('owner',))
        store._scope(scope, project.id)
        command = AgentContextIssue(instance_id=instance_id, expected_revision=expected_revision, key=key)
        store._host_guard()
        if Projects(store).resolve(Path(project.root.path)) != project:
            raise StoreError('project_changed')
        store._host_guard()
        request = {'agent_context_v1': command.model_dump(mode='json'), 'project_id': project.id,
            'project_revision': project.revision}
        with store.transaction():
            store._host_sql()
            VerificationRegistry(store)._project_sql(project)
            found, saved, request_hash = store._replay(scope, key, request)
            if found:
                context = AgentRequestContext.model_validate(saved['agent_request_context_v1'])
                if not self._same_host(context):
                    raise StoreError('agent_context_stale')
                return AgentRequestCredential(context=context, credential=self._credential(context))
            instance = store._instance(instance_id)
            profile = store._profile(instance.profile_id)
            if instance.revision != expected_revision:
                raise StoreError('revision')
            if project.id not in profile.project_ids:
                raise StoreError('scope')
            if (instance.state != 'idle' or instance.current_attempt_id is not None
                    or instance.profile_version != profile.version):
                raise StoreError('agent_context_stale')
            identity, now = uuid4().hex, store.clock()
            context = AgentRequestContext(id=identity, project_id=project.id, project_revision=project.revision,
                project_hash=digest(project.model_dump(mode='json')), instance_id=instance.id,
                instance_revision=instance.revision, profile_id=profile.id, profile_version=profile.version,
                profile_hash=digest(profile.model_dump(mode='json')), owner_id=scope.actor_id,
                host_registration_id=store.host.registration.id, host_session_id=store.host.session.id,
                host_generation=store.host.session.generation, credential_hash='0' * 64,
                created_at=now, expires_at=now + 300.)
            credential = self._credential(context)
            context = changed(context, credential_hash=_hash(credential))
            store.host._agent_request_deadlines[identity] = time.monotonic() + 300.
            store.db.execute('INSERT INTO agent_request_contexts VALUES (?,?,?,?)',
                (context.id, project.id, instance.id, context.model_dump_json()))
            store.db.execute('INSERT INTO agent_request_heads VALUES (?,?) ON CONFLICT(instance_id) '
                'DO UPDATE SET context_id=excluded.context_id', (instance.id, context.id))
            store._remember(scope, key, project.id, request_hash, {'agent_request_context_v1': context.model_dump(mode='json')})
            return AgentRequestCredential(context=context, credential=credential)

    def _authorize_sql(self, instance_id, credential) -> AgentRequestContext:
        store = self.store
        store._host_sql()
        identity, separator, proof = credential.partition('.')
        if (not separator or len(identity) != 32 or len(proof) != 64
                or any(letter not in '0123456789abcdef' for letter in identity + proof)):
            raise StoreError('agent_identity')
        context = store._load('agent_request_contexts', identity, AgentRequestContext)
        head = store.db.execute("""SELECT
            (SELECT context_id FROM agent_request_heads WHERE instance_id=?) AS context_id,
            (SELECT json_extract(result,'$.agent_request_context_v1.id') FROM commands
                WHERE json_extract(result,'$.agent_request_context_v1.instance_id')=?
                ORDER BY rowid DESC LIMIT 1) AS latest_id""", (instance_id, instance_id)).fetchone()
        instance = store._instance(instance_id)
        profile = store._profile(instance.profile_id)
        project = Projects(store).get(context.project_id)
        if (context.instance_id != instance_id or not self._same_host(context)
                or head['context_id'] != context.id or head['latest_id'] != context.id
                or not hmac.compare_digest(self._credential(context), credential)
                or not hmac.compare_digest(context.credential_hash, _hash(credential))
                or (instance.profile_id, instance.profile_version) != (context.profile_id, context.profile_version)
                or digest(profile.model_dump(mode='json')) != context.profile_hash
                or project.state != 'active' or project.revision != context.project_revision
                or digest(project.model_dump(mode='json')) != context.project_hash):
            raise StoreError('agent_identity')
        return context

    def authorize(self, instance_id, credential) -> AgentRequestContext:
        store = self.store
        store._host_guard()
        try:
            context = self._authorize_sql(instance_id, credential)
            project = Projects(store).get(context.project_id)
            if Projects(store).resolve(Path(project.root.path)) != project:
                raise StoreError('agent_identity')
            return self._authorize_sql(instance_id, credential)
        except (OSError, ValueError) as error:
            if isinstance(error, StoreError) and error.code.startswith('host_'):
                raise
            raise StoreError('agent_identity') from error
