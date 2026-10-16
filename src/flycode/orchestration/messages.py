"""Bounded addressed data, durable outbox and transactional receipt deduplication."""

import hashlib
import json
from typing import cast
from uuid import uuid4

from ..domain.orchestration import AgentMessage, MessageDraft, MessageReceipt
from .store import Store, StoreError, covers


class Messages:
    def __init__(self, store: Store):
        self.store = store

    def _message(self, identity):
        return self.store._load('messages', identity, AgentMessage)

    def _current(self, scope, attempt, *, running=True):
        try:
            _, task, workflow = self.store._owned(scope, attempt.id, attempt.fence, running=running)
            return workflow.state == 'running' and not task.paused and not self.store._counter('queue_paused')
        except StoreError:
            return False

    def recipients(self, scope, sender, identities, *, parent=None):
        task = self.store.task(scope, sender.task_id)
        result = []
        auditing = not self._current(scope, sender, running=False)
        for identity in identities:
            instance = self.store.instance(scope, identity)
            if parent is not None and identity == parent.sender_id:
                identity_attempt = parent.attempt_id
            elif parent is not None and identity == parent.recipient_id:
                identity_attempt = parent.recipient_attempt_id
            else:
                identity_attempt = instance.current_attempt_id
            if identity_attempt is None:
                raise StoreError('recipient', 'Recipient has no workflow assignment')
            attempt = self.store._attempt(identity_attempt)
            target = self.store.task(scope, attempt.task_id)
            if attempt.workflow_id != sender.workflow_id:
                raise StoreError('recipient_workflow')
            if not auditing and self._current(scope, attempt, running=False) and (
                    task.permissions is None or target.permissions is None
                    or not covers(target.permissions.read_scopes, task.permissions.read_scopes)):
                raise StoreError('message_scope', 'Recipient cannot read the sender task scope')
            result.append((attempt, target))
        return result

    def _parent(self, scope, sender, draft):
        if draft.causation_id is None:
            if draft.content.kind == 'answer':
                raise StoreError('causation', 'An answer requires its addressed question')
            return None
        parent = self._message(draft.causation_id)
        if (parent.workflow_id != sender.workflow_id
                or sender.instance_id not in {parent.sender_id, parent.recipient_id}):
            raise StoreError('causation', 'Caller is not a participant in this workflow message')
        if draft.content.kind == 'answer' and (parent.content.kind != 'question'
                or parent.recipient_id != sender.instance_id or draft.recipients != (parent.sender_id,)):
            raise StoreError('causation', 'Answer must address the sender of its question')
        return parent

    def _question(self, message):
        return self.store.db.execute('SELECT * FROM message_questions WHERE workflow_id=? AND correlation_id=?',
            (message.workflow_id, message.correlation_id)).fetchone()

    def _reason(self, scope, message):
        if message.expires_at <= self.store.clock():
            return 'expired'
        sender, recipient = self.store._attempt(message.attempt_id), self.store._attempt(message.recipient_attempt_id)
        if not self._current(scope, sender):
            return 'stale_sender'
        if not self._current(scope, recipient, running=False):
            return 'stale_recipient'
        source, target = self.store.task(scope, sender.task_id), self.store.task(scope, recipient.task_id)
        if (source.permissions is None or target.permissions is None
                or not covers(target.permissions.read_scopes, source.permissions.read_scopes)
                or any(source.input_artifacts.get(item.id) != item.sha256
                       or target.input_artifacts.get(item.id) != item.sha256 for item in message.artifacts)):
            return 'message_scope'
        if message.causation_id is not None:
            parent = self._message(message.causation_id)
            if ((parent.recipient_id == sender.instance_id and parent.recipient_attempt_id != sender.id)
                    or (parent.sender_id == sender.instance_id and parent.attempt_id != sender.id)
                    or (parent.sender_id == recipient.instance_id and parent.attempt_id != recipient.id)
                    or (parent.recipient_id == recipient.instance_id and parent.recipient_attempt_id != recipient.id)):
                return 'stale_parent'
            if message.content.kind == 'answer':
                question = self._question(message)
                if question is None or question['batch_id'] != parent.batch_id or question['answer_id'] is not None:
                    return 'question_closed'
        return None

    def _receipt(self, scope, message, reason):
        store = self.store
        existing = store.db.execute('SELECT data FROM message_inbox WHERE message_id=?', (message.id,)).fetchone()
        if existing:
            return MessageReceipt.model_validate_json(existing['data'])
        receipt = MessageReceipt(message_id=message.id, recipient_attempt_id=message.recipient_attempt_id,
            received_at=store.clock(), outcome='audit' if reason else 'received', reason=reason)
        if reason is None and message.content.kind == 'answer':
            store.db.execute('UPDATE message_questions SET answer_id=? WHERE workflow_id=? AND correlation_id=?',
                (message.id, message.workflow_id, message.correlation_id))
        store.db.execute('INSERT INTO message_inbox VALUES (?,?)', (message.id, receipt.model_dump_json()))
        store.db.execute('UPDATE message_outbox SET delivered_at=? WHERE message_id=?', (store.clock(), message.id))
        recipient = store._attempt(message.recipient_attempt_id)
        store._event(scope, store._workflow(message.workflow_id), 'message_discarded' if reason else 'message_received',
            task=store.task(scope, recipient.task_id), attempt=recipient, message_id=message.id, reason=reason)
        return receipt

    def send(self, scope, attempt_id, fence, draft: MessageDraft) -> list[AgentMessage]:
        store = self.store
        store._role(scope, ('runtime',))
        draft = MessageDraft.model_validate(draft.model_dump())
        with store.transaction():
            sender = store._attempt(attempt_id)
            task = store.task(scope, sender.task_id)
            workflow = store._workflow(sender.workflow_id)
            if sender.fence != fence:
                raise StoreError('fence')
            digest = hashlib.sha256(json.dumps({'attempt_id': sender.id, 'fence': fence,
                'draft': draft.model_dump(mode='json')}, sort_keys=True, allow_nan=False).encode()).hexdigest()
            previous = store.db.execute('SELECT * FROM message_batches WHERE attempt_id=? AND key=?',
                (sender.id, draft.idempotency_key)).fetchone()
            if previous:
                if previous['request_hash'] != digest:
                    raise StoreError('idempotency', 'Key already belongs to a different message')
                return [self._message(identity) for identity in json.loads(previous['message_ids'])]
            parent = self._parent(scope, sender, draft)
            targets = self.recipients(scope, sender, draft.recipients, parent=parent)
            hops = parent.hops + 1 if parent else 0
            if hops > 4:
                raise StoreError('hop_limit')
            count = store.db.execute('SELECT COUNT(*) FROM messages WHERE workflow_id=?', (workflow.id,)).fetchone()[0]
            if count + len(targets) > 200:
                raise StoreError('message_budget')
            now, batch = store.clock(), uuid4().hex
            expiration = min(now + draft.ttl_seconds, workflow.deadline_at or now + draft.ttl_seconds,
                             parent.expires_at if parent else now + draft.ttl_seconds)
            correlation = parent.correlation_id if parent else batch
            messages = []
            for recipient, target in targets:
                if self._current(scope, sender) and self._current(scope, recipient, running=False) and any(
                        task.input_artifacts.get(item.id) != item.sha256 or target.input_artifacts.get(item.id) != item.sha256
                        for item in draft.artifacts):
                    raise StoreError('artifact_scope')
                identity = uuid4().hex
                sequence = store.db.execute('INSERT INTO messages(id,workflow_id,recipient_attempt_id,data) VALUES (?,?,?,?)',
                    (identity, workflow.id, recipient.id, '{}')).lastrowid
                message = AgentMessage(id=identity, batch_id=batch, sequence=cast(int, sequence), project_id=task.project_id,
                    workflow_id=workflow.id, task_id=task.id, attempt_id=sender.id, sender_id=sender.instance_id,
                    sender_fence=fence, recipient_id=recipient.instance_id, recipient_attempt_id=recipient.id,
                    recipient_fence=recipient.fence, correlation_id=correlation, causation_id=draft.causation_id,
                    hops=hops, created_at=now, expires_at=expiration, idempotency_key=draft.idempotency_key,
                    content=draft.content, artifacts=draft.artifacts)
                data = message.model_dump_json()
                if len(data.encode()) > 16 * 1024:
                    raise StoreError('message_size')
                store.db.execute('UPDATE messages SET data=? WHERE id=?', (data, identity))
                store.db.execute('INSERT INTO message_outbox(message_id) VALUES (?)', (identity,))
                store._event(scope, workflow, 'message_sent', task=task, attempt=sender, message_id=identity,
                    recipient_id=recipient.instance_id, message_kind=message.content.kind,
                    content_hash=hashlib.sha256(data.encode()).hexdigest())
                messages.append(message)
            current_question = next((item for item in messages if self._reason(scope, item) is None), None)
            if draft.content.kind == 'question' and current_question is not None:
                question = self._question(current_question)
                if question is not None and question['answer_id'] is None and question['expires_at'] > now:
                    raise StoreError('question_open')
                store.db.execute('INSERT INTO message_questions VALUES (?,?,?,?,NULL) ON CONFLICT(workflow_id,correlation_id) '
                    'DO UPDATE SET batch_id=excluded.batch_id,expires_at=excluded.expires_at,answer_id=NULL',
                    (workflow.id, correlation, batch, expiration))
            for message in messages:
                reason = self._reason(scope, message)
                if reason:
                    self._receipt(scope, message, reason)
            store.db.execute('INSERT INTO message_batches VALUES (?,?,?,?)', (sender.id, draft.idempotency_key,
                digest, json.dumps([item.id for item in messages])))
            return messages

    def pending(self, scope, attempt_id, fence) -> list[AgentMessage]:
        store = self.store
        with store.transaction():
            store._owned(scope, attempt_id, fence, running=True)
            rows = store.db.execute('SELECT m.data FROM messages m JOIN message_outbox o ON o.message_id=m.id '
                'WHERE m.recipient_attempt_id=? AND o.delivered_at IS NULL ORDER BY m.sequence LIMIT 200', (attempt_id,)).fetchall()
            result = []
            for row in rows:
                message = AgentMessage.model_validate_json(row['data'])
                reason = self._reason(scope, message)
                if reason:
                    self._receipt(scope, message, reason)
                else:
                    result.append(message)
            return result

    def acknowledge(self, scope, attempt_id, fence, message_id) -> MessageReceipt:
        store = self.store
        store._role(scope, ('runtime',))
        with store.transaction():
            attempt = store._attempt(attempt_id)
            store.task(scope, attempt.task_id)
            message = self._message(message_id)
            if message.recipient_attempt_id != attempt.id or message.recipient_fence != fence or attempt.fence != fence:
                raise StoreError('recipient')
            return self._receipt(scope, message, self._reason(scope, message))

    def journal(self, scope, workflow_id) -> list[AgentMessage]:
        self.store.workflow(scope, workflow_id)
        return [AgentMessage.model_validate_json(row['data']) for row in self.store.db.execute(
            'SELECT data FROM messages WHERE workflow_id=? ORDER BY sequence LIMIT 200', (workflow_id,))]
