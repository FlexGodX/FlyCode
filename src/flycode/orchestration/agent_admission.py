"""Durable pull requests share bound dispatch and its execution pool."""

import hashlib
import hmac
from uuid import uuid4

from ..domain.agent_requests import (
    AgentRequestContext,
    AgentTaskRequest,
    AgentTaskRequestReceipt,
)
from ..domain.orchestration import AccessScope, ClaimResult
from ..verification.snapshot import digest
from .agent_requests import AgentRequests
from .store import StoreError, changed


class AgentTaskRequests:
    def __init__(self, store):
        self.store, self.identities = store, AgentRequests(store)

    def _seal(self, receipt):
        material = 'agent-task-request-v1:' + digest(receipt.model_dump(mode='json', exclude={'seal'}))
        return hmac.new(self.store.host._agent_request_secret, material.encode(), hashlib.sha256).hexdigest()

    def _load(self, identity):
        row = self.store.db.execute('SELECT * FROM agent_task_requests WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise StoreError('not_found')
        try:
            receipt = AgentTaskRequestReceipt.model_validate_json(row['data'])
        except ValueError as error:
            raise StoreError('agent_request_changed') from error
        if ((row['id'], row['context_id'], row['key'], row['state'], row['request_hash'])
                != (receipt.id, receipt.context_id, receipt.request.idempotency_key, receipt.state,
                    digest(receipt.request.model_dump(mode='json')))
                or not hmac.compare_digest(receipt.seal, self._seal(receipt))):
            raise StoreError('agent_request_changed')
        return receipt

    @staticmethod
    def scope(context):
        return AccessScope(actor_id='agent-context:' + context.id, project_ids={context.project_id}, role='runtime')

    def _context_sql(self, receipt):
        context = self.store._load('agent_request_contexts', receipt.context_id, AgentRequestContext)
        current = self.identities._authorize_sql(receipt.instance_id, self.identities._credential(context))
        if receipt.project_id != current.project_id:
            raise StoreError('agent_identity')
        return current

    def submit(self, instance_id, credential, request) -> AgentTaskRequestReceipt:
        request = AgentTaskRequest.model_validate(request)
        context = self.identities.authorize(instance_id, credential)
        store = self.store
        with store.transaction():
            context = self.identities._authorize_sql(instance_id, credential)
            row = store.db.execute('SELECT id FROM agent_task_requests WHERE context_id=? AND key=?',
                (context.id, request.idempotency_key)).fetchone()
            if row:
                receipt = self._load(row['id'])
                if receipt.request != request:
                    raise StoreError('idempotency')
                return receipt
            instance = store._instance(instance_id)
            if (instance.revision != request.expected_instance_revision or instance.revision != context.instance_revision
                    or instance.state != 'idle' or instance.current_attempt_id is not None):
                raise StoreError('agent_context_stale')
            profile = store._profile(context.profile_id)
            if request.capabilities is not None and not set(request.capabilities) <= set(profile.capabilities):
                raise StoreError('capabilities')
            if request.preferred_task_id is not None:
                try:
                    task = store.task(self.scope(context), request.preferred_task_id)
                except StoreError as error:
                    raise StoreError('scope') from error
                if task.revision != request.expected_task_revision:
                    raise StoreError('revision')
            previous = store.db.execute('SELECT id FROM agent_task_requests WHERE context_id=? ORDER BY rowid DESC LIMIT 1',
                (context.id,)).fetchone()
            if previous:
                latest = self._load(previous['id'])
                if latest.state == 'queued':
                    raise StoreError('request_pending')
                if latest.next_request_at is not None and store.clock() < latest.next_request_at:
                    raise StoreError('request_backoff')
            receipt = AgentTaskRequestReceipt(id=uuid4().hex, context_id=context.id, project_id=context.project_id,
                instance_id=instance_id, request=request, created_at=store.clock(), seal='0' * 64)
            receipt = changed(receipt, seal=self._seal(receipt))
            store.db.execute('INSERT INTO agent_task_requests VALUES (?,?,?,?,?,?)', (receipt.id, context.id,
                request.idempotency_key, receipt.state, digest(request.model_dump(mode='json')), receipt.model_dump_json()))
            return receipt

    def get(self, instance_id, credential, identity) -> AgentTaskRequestReceipt:
        context = self.identities.authorize(instance_id, credential)
        receipt = self._load(identity)
        if receipt.context_id != context.id or receipt.instance_id != instance_id or receipt.project_id != context.project_id:
            raise StoreError('scope')
        return receipt

    def pending(self, scope=None):
        self.store._host_guard()
        return tuple(row['id'] for row in self.store.db.execute("""SELECT r.id,c.project_id
            FROM agent_task_requests r JOIN agent_request_contexts c ON c.id=r.context_id
            WHERE r.state='queued' ORDER BY r.rowid""")
            if scope is None or row['project_id'] in scope.project_ids)

    def deny(self, scope, identity, reason):
        store = self.store
        store._role(scope, ('runtime',))
        store._host_guard()
        with store.transaction():
            store._host_sql()
            row = store.db.execute("""SELECT r.state,c.project_id FROM agent_task_requests r
                JOIN agent_request_contexts c ON c.id=r.context_id WHERE r.id=?""", (identity,)).fetchone()
            if row is None:
                raise StoreError('not_found')
            store._scope(scope, row['project_id'])
            if row['state'] != 'queued':
                return
            try:
                receipt = self._load(identity)
            except StoreError as error:
                if error.code != 'agent_request_changed':
                    raise
                # A previous Host's seal or altered record never gains authority.
                # Retain the original bytes; only close its queue gate.
                store.db.execute("UPDATE agent_task_requests SET state='denied' WHERE id=?", (identity,))
                return
            saved = changed(receipt, state='denied', completed_at=store.clock(), error_code=reason)
            saved = changed(saved, seal=self._seal(saved))
            store.db.execute('UPDATE agent_task_requests SET state=?,data=? WHERE id=?',
                (saved.state, saved.model_dump_json(), identity))

    def prepare(self, scope, identity):
        self.store._host_guard()
        receipt = self._load(identity)
        self.store._scope(scope, receipt.project_id)
        context = self._context_sql(receipt)
        self.identities.authorize(receipt.instance_id, self.identities._credential(context))
        return receipt, self.scope(context)

    def admission(self, scope, identity):
        receipt = self._load(identity)
        self.store._scope(scope, receipt.project_id)
        context = self._context_sql(receipt)
        if receipt.state != 'queued':
            return receipt
        instance = self.store._instance(receipt.instance_id)
        if (instance.revision != context.instance_revision
                or receipt.request.expected_instance_revision != context.instance_revision
                or instance.state != 'idle' or instance.current_attempt_id is not None):
            raise StoreError('agent_context_stale')
        if receipt.request.preferred_task_id is not None:
            task = self.store.task(self.scope(context), receipt.request.preferred_task_id)
            if task.revision != receipt.request.expected_task_revision:
                raise StoreError('revision')
        return receipt

    def finish(self, receipt, result: ClaimResult):
        now = self.store.clock()
        saved = changed(receipt, state='assigned' if result.attempt else 'no_eligible_task', result=result,
            completed_at=now, next_request_at=None if result.attempt else now + result.retry_after_s)
        saved = changed(saved, seal=self._seal(saved))
        self.store.db.execute('UPDATE agent_task_requests SET state=?,data=? WHERE id=? AND state=?',
            (saved.state, saved.model_dump_json(), saved.id, 'queued'))
        return saved

    def hint(self, instance):
        row = self.store.db.execute('''SELECT r.id FROM agent_request_heads h JOIN agent_task_requests r
            ON r.context_id=h.context_id WHERE h.instance_id=? ORDER BY r.rowid DESC LIMIT 1''', (instance.id,)).fetchone()
        if row is None:
            return None
        try:
            receipt = self._load(row['id'])
            context = self._context_sql(receipt)
            return receipt if context.instance_revision == instance.revision else None
        except StoreError as error:
            if error.code.startswith('host_'):
                raise
            return None
