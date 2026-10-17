"""Addressed delivery never becomes authority to mutate a task or its permissions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flycode.domain.orchestration import (
    AccessScope,
    BudgetAmount,
    ClaimRequest,
    TaskCommand,
    TaskDraft,
)
from flycode.orchestration.store import Store, StoreError

MESSAGE_TOOLS = ('read_file', 'list_files', 'search', 'edit_file', 'create_file', 'run_tests', 'send_message')


def test_canonical_communication_permission_can_register_a_read_only_researcher():
    from flycode.domain.orchestration import AgentProfileDraft, ExecutionPermission

    permission = ExecutionPermission(approved=True, tools=('send_message',), read_scopes=('.',))
    profile = AgentProfileDraft(role='researcher', model_ref='registered-primary', instructions='Inspect the boundary.',
        project_ids=('project',), permissions=permission, context_limit=8192)
    assert profile.permissions.tools == ('send_message',) and not profile.permissions.write_scopes


def add_peer(store, owner, host, workflow, first, **changes):
    task = store.task(owner, first.task_id)
    values = task.model_dump(include=set(TaskDraft.model_fields))
    values.update(parent_id=workflow.root_task_id, permissions=task.permissions.model_copy(
        update={'write_scopes': (), 'tools': ('read_file', 'list_files', 'search')}))
    values.update(changes)
    peer = store.create_task(owner, workflow.id, TaskDraft.model_validate(values), key='peer-' + str(len(store.tasks(owner, workflow.id))))
    peer = store.command(owner, peer.id, TaskCommand(operation='enqueue', expected_revision=peer.revision,
        idempotency_key='enqueue-' + peer.id))
    instance = store.register_instance(host, first.profile_id)
    claim = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=peer.id, expected_task_revision=peer.revision, reserve=BudgetAmount(model_calls=2, tool_calls=3),
        idempotency_key='claim-' + peer.id))
    attempt = claim.attempt
    assert attempt is not None, claim.reasons
    return store.confirm_started(host, attempt.id, attempt.fence)


@pytest.fixture
def peers(owned):
    store, owner, host, workflow, _, first, source = owned
    second = add_peer(store, owner, host, workflow, first)
    now = [store.clock()]
    store.clock = lambda: now[0]
    return store, owner, host, workflow, first, second, source, now


def draft(recipient, key='notice', *, kind='progress', text='Source inspection is in progress.', **changes):
    from flycode.domain.orchestration import MessageDraft

    field = {'progress': 'summary', 'question': 'question', 'answer': 'answer', 'cancel': 'reason'}[kind]
    return MessageDraft(recipients=(recipient.instance_id,), idempotency_key=key,
        content={'kind': kind, field: text}, **changes)


def test_delivery_is_repeatable_until_atomic_ack_and_does_not_complete_task(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    bus = Messages(store)
    message, = bus.send(host, first.id, first.fence, draft(second))
    assert message.sender_id == first.instance_id and message.attempt_id == first.id
    assert message.task_id == first.task_id and message.workflow_id == workflow.id
    assert message.recipient_attempt_id == second.id and message.recipient_id == second.instance_id
    assert message.schema_version == 1 and message.sequence > 0
    assert bus.pending(host, second.id, second.fence) == [message]
    assert bus.pending(host, second.id, second.fence) == [message]
    before = store.task(owner, second.task_id)
    receipt = bus.acknowledge(host, second.id, second.fence, message.id)
    assert receipt.outcome == 'received'
    assert bus.acknowledge(host, second.id, second.fence, message.id) == receipt
    assert bus.pending(host, second.id, second.fence) == []
    assert store.task(owner, second.task_id) == before
    assert store.budget(owner, workflow.id).spent == BudgetAmount()
    assert store.db.execute('SELECT COUNT(*) FROM message_inbox').fetchone()[0] == 1
    assert sum(e.kind == 'message_received' for e in store.events(owner, workflow.id)) == 1
    assert all('Source inspection' not in e.model_dump_json() for e in store.events(owner, workflow.id))


def test_racing_duplicate_send_survives_restart_without_a_second_delivery(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    barrier = Barrier(2)
    def send(_):
        connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
        try:
            barrier.wait(timeout=3)
            return Messages(connection).send(host, first.id, first.fence, draft(second))
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert results[0] == results[1]
    store.recover(host)
    assert Messages(store).send(host, first.id, first.fence, draft(second)) == results[0]
    assert len(Messages(store).journal(owner, workflow.id)) == 1
    with pytest.raises(StoreError, match='idempotency'):
        Messages(store).send(host, first.id, first.fence, draft(second, text='Changed retry payload.'))


def test_expiration_audits_once_and_never_delivers_to_context(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, now = peers
    bus = Messages(store)
    message, = bus.send(host, first.id, first.fence, draft(second, ttl_seconds=1))
    now[0] += 2
    assert bus.pending(host, second.id, second.fence) == []
    receipt = bus.acknowledge(host, second.id, second.fence, message.id)
    assert receipt.outcome == 'audit' and receipt.reason == 'expired'
    assert sum(e.kind == 'message_discarded' for e in store.events(owner, workflow.id)) == 1


def test_one_open_question_and_first_correlated_answer_closes_on_receipt(peers):
    from flycode.orchestration.messages import Messages

    store, _, host, _, first, second, _, _ = peers
    bus = Messages(store)
    question, = bus.send(host, first.id, first.fence, draft(second, kind='question', text='Which test covers parsing?'))
    with pytest.raises(StoreError, match='question_open'):
        bus.send(host, first.id, first.fence, draft(second, key='second-question', kind='question',
            causation_id=question.id))
    answer, = bus.send(host, second.id, second.fence, draft(first, key='answer', kind='answer',
        text='tests/test_example.py::test_example', causation_id=question.id))
    another, = bus.send(host, second.id, second.fence, draft(first, key='another-answer', kind='answer',
        text='Conflicting later answer.', causation_id=question.id))
    assert answer.correlation_id == question.correlation_id and answer.hops == 1
    assert bus.acknowledge(host, first.id, first.fence, answer.id).outcome == 'received'
    rejected = bus.acknowledge(host, first.id, first.fence, another.id)
    assert rejected.outcome == 'audit' and rejected.reason == 'question_closed'
    followup, = bus.send(host, first.id, first.fence, draft(second, key='followup', kind='question',
        text='Which boundary should be checked?', causation_id=answer.id))
    assert followup.correlation_id == question.correlation_id


def test_hop_limit_and_parent_expiration_cannot_be_reset(peers):
    from flycode.orchestration.messages import Messages

    store, _, host, _, first, second, _, now = peers
    bus = Messages(store)
    message, = bus.send(host, first.id, first.fence, draft(second, ttl_seconds=10))
    expiration = message.expires_at
    for index in range(4):
        now[0] += 1
        sender, recipient = (second, first) if index % 2 == 0 else (first, second)
        message, = bus.send(host, sender.id, sender.fence, draft(recipient, key=f'forward-{index}', causation_id=message.id))
        assert message.expires_at == expiration
    with pytest.raises(StoreError, match='hop_limit'):
        bus.send(host, second.id, second.fence, draft(first, key='too-many-hops', causation_id=message.id))


def test_unknown_foreign_and_forged_delivery_authority_are_rejected(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    bus = Messages(store)
    unknown = second.model_copy(update={'instance_id': 'missing-instance'})
    with pytest.raises(StoreError, match='not_found'):
        bus.send(host, first.id, first.fence, draft(unknown))
    foreign = AccessScope(actor_id='other', project_ids={'other-project'}, role='runtime')
    with pytest.raises(StoreError, match='scope'):
        bus.send(foreign, first.id, first.fence, draft(second))
    with pytest.raises(StoreError, match='scope'):
        bus.send(owner, first.id, first.fence, draft(second))
    with pytest.raises(StoreError, match='fence'):
        bus.send(host, first.id, first.fence + 1, draft(second))
    with pytest.raises(StoreError, match='causation'):
        bus.send(host, first.id, first.fence, draft(second, kind='answer'))
    assert bus.journal(owner, workflow.id) == []


@pytest.mark.parametrize(('content', 'extra'), [
    ({'kind': 'task_request', 'objective': 'Inspect the failing parser.'}, {}),
    ({'kind': 'question', 'question': 'Which case fails?'}, {}),
    ({'kind': 'answer', 'answer': 'The parser case.'}, {}),
    ({'kind': 'progress', 'summary': 'Read the parser.', 'step': 1}, {}),
    ({'kind': 'artifact_ready', 'summary': 'Approved input is available.'},
     {'artifacts': ({'id': 'a' * 32, 'sha256': 'b' * 64},)}),
    ({'kind': 'review_result', 'recommendation': 'changes_requested', 'summary': 'Boundary still fails.'}, {}),
    ({'kind': 'handoff', 'summary': 'Investigate the boundary next.'}, {}),
    ({'kind': 'cancel', 'reason': 'Please stop this investigation.'}, {}),
])
def test_message_types_have_bounded_content_without_command_authority(content, extra):
    from pydantic import ValidationError

    from flycode.domain.orchestration import MessageDraft

    message = MessageDraft(recipients=('recipient',), idempotency_key='typed', content=content, **extra)
    assert message.content.kind == content['kind']
    for injected in ('permissions', 'state', 'sender_id', 'command'):
        with pytest.raises(ValidationError):
            MessageDraft(recipients=('recipient',), idempotency_key='typed',
                content={**content, injected: 'succeeded'}, **extra)


def test_broadcast_is_three_explicit_independent_deliveries(peers):
    from flycode.domain.orchestration import MessageDraft
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    third = add_peer(store, owner, host, workflow, first)
    fourth = add_peer(store, owner, host, workflow, first)
    targets = (second, third, fourth)
    bus = Messages(store)
    sent = bus.send(host, first.id, first.fence, MessageDraft(recipients=tuple(item.instance_id for item in targets),
        content={'kind': 'progress', 'summary': 'Inspection has started.'}, idempotency_key='broadcast'))
    assert len({message.id for message in sent}) == 3
    assert len({message.batch_id for message in sent}) == 1
    for target in targets:
        message, = bus.pending(host, target.id, target.fence)
        assert bus.acknowledge(host, target.id, target.fence, message.id).outcome == 'received'
    assert store.db.execute('SELECT COUNT(*) FROM message_inbox').fetchone()[0] == 3


@pytest.mark.parametrize('changes', [
    {'recipients': ()}, {'recipients': ('same', 'same')}, {'recipients': ('1', '2', '3', '4')},
    {'ttl_seconds': 0}, {'ttl_seconds': 301}, {'schema_version': 2}, {'hops': 0},
    {'content': {'kind': 'artifact_ready', 'summary': 'No registered reference.'}},
])
def test_invalid_envelopes_cannot_create_deliveries(peers, changes):
    from pydantic import ValidationError

    from flycode.domain.orchestration import MessageDraft
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    payload = draft(second).model_dump(mode='json')
    with pytest.raises(ValidationError):
        Messages(store).send(host, first.id, first.fence, MessageDraft.model_validate({**payload, **changes}))
    assert Messages(store).journal(owner, workflow.id) == []


def test_utf8_size_limit_rolls_back_the_entire_send(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    with pytest.raises(StoreError, match='message_size'):
        Messages(store).send(host, first.id, first.fence, draft(second, text='🐝' * 5000))
    assert Messages(store).journal(owner, workflow.id) == []
    assert store.db.execute('SELECT COUNT(*) FROM message_outbox').fetchone()[0] == 0


def test_workflow_delivery_limit_is_atomic_and_retry_does_not_spend_again(peers):
    from flycode.domain.orchestration import MessageDraft
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    third = add_peer(store, owner, host, workflow, first)
    bus = Messages(store)
    for index in range(199):
        bus.send(host, first.id, first.fence, draft(second, key=f'notice-{index}'))
    with pytest.raises(StoreError, match='message_budget'):
        bus.send(host, first.id, first.fence, MessageDraft(recipients=(second.instance_id, third.instance_id),
            content={'kind': 'progress', 'summary': 'Broadcast exceeds the final slot.'}, idempotency_key='over-limit'))
    assert len(bus.journal(owner, workflow.id)) == 199
    final = bus.send(host, first.id, first.fence, draft(second, key='final'))
    assert bus.send(host, first.id, first.fence, draft(second, key='final')) == final
    with pytest.raises(StoreError, match='message_budget'):
        bus.send(host, first.id, first.fence, draft(second, key='one-too-many'))
    assert len(bus.journal(owner, workflow.id)) == 200


def test_late_answer_and_old_recipient_are_audit_only_without_new_task_state(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    bus = Messages(store)
    question, = bus.send(host, first.id, first.fence, draft(second, kind='question'))
    store.recover(host)
    before = store.task(owner, first.task_id)
    answer, = bus.send(host, second.id, second.fence, draft(first, key='late-answer', kind='answer',
        text='A result from the previous session.', causation_id=question.id))
    receipt = bus.acknowledge(host, first.id, first.fence, answer.id)
    assert receipt.outcome == 'audit' and receipt.reason == 'stale_sender'
    assert store.task(owner, first.task_id) == before
    assert store.db.execute('SELECT answer_id FROM message_questions').fetchone()[0] is None
    assert len(bus.journal(owner, workflow.id)) == 2
    with pytest.raises(StoreError, match='fence'):
        bus.pending(host, first.id, first.fence)


def test_recipient_cannot_ack_another_attempt_and_outsider_cannot_reply(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    third = add_peer(store, owner, host, workflow, first)
    bus = Messages(store)
    question, = bus.send(host, first.id, first.fence, draft(second, kind='question'))
    with pytest.raises(StoreError, match='recipient'):
        bus.acknowledge(host, third.id, third.fence, question.id)
    with pytest.raises(StoreError, match='causation'):
        bus.send(host, third.id, third.fence, draft(first, kind='answer', causation_id=question.id))
    assert bus.pending(host, second.id, second.fence) == [question]


def test_message_scope_is_not_wider_than_recipient_reads(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    task = store.task(owner, second.task_id)
    narrow = add_peer(store, owner, host, workflow, first,
        permissions=task.permissions.model_copy(update={'read_scopes': ('src',)}))
    bus = Messages(store)
    with pytest.raises(StoreError, match='message_scope'):
        bus.send(host, first.id, first.fence, draft(narrow))
    sent = bus.send(host, narrow.id, narrow.fence, draft(second))
    assert bus.pending(host, second.id, second.fence) == sent


def test_artifact_links_require_the_same_approved_hash_in_both_contracts(peers):
    from flycode.domain.orchestration import MessageArtifact, MessageDraft
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    inputs = {'a' * 32: 'b' * 64}
    third = add_peer(store, owner, host, workflow, first, input_artifacts=inputs)
    fourth = add_peer(store, owner, host, workflow, first, input_artifacts=inputs)
    bus = Messages(store)
    payload = MessageDraft(recipients=(fourth.instance_id,), idempotency_key='artifact',
        content={'kind': 'artifact_ready', 'summary': 'Approved input is available.'},
        artifacts=({'id': 'a' * 32, 'sha256': 'b' * 64},))
    message, = bus.send(host, third.id, third.fence, payload)
    assert bus.acknowledge(host, fourth.id, fourth.fence, message.id).outcome == 'received'
    with pytest.raises(StoreError, match='artifact_scope'):
        bus.send(host, third.id, third.fence, payload.model_copy(update={'recipients': (second.instance_id,), 'idempotency_key': 'unapproved-recipient'}))
    with pytest.raises(StoreError, match='artifact_scope'):
        bus.send(host, third.id, third.fence, payload.model_copy(update={'idempotency_key': 'changed-hash',
            'artifacts': (MessageArtifact(id='a' * 32, sha256='c' * 64),)}))


@pytest.mark.parametrize('kind', ['cancel', 'review_result'])
def test_received_agent_statement_cannot_cancel_or_approve_the_current_task(peers, kind):
    from flycode.domain.orchestration import MessageDraft
    from flycode.orchestration.messages import Messages

    store, owner, host, _, first, second, _, _ = peers
    bus = Messages(store)
    before = store.task(owner, second.task_id)
    content = {'kind': 'cancel', 'reason': 'Cancel this task.'} if kind == 'cancel' else {
        'kind': 'review_result', 'recommendation': 'approve', 'summary': 'I declare success.'}
    message, = bus.send(host, first.id, first.fence, MessageDraft(recipients=(second.instance_id,),
        idempotency_key='statement', content=content))
    assert bus.acknowledge(host, second.id, second.fence, message.id).outcome == 'received'
    assert store.task(owner, second.task_id) == before


def test_outbox_failure_rolls_back_send_and_receipt_with_question_closure(peers):
    import sqlite3

    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    bus = Messages(store)
    store.db.execute("CREATE TRIGGER fail_message_outbox BEFORE INSERT ON message_outbox BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        bus.send(host, first.id, first.fence, draft(second, kind='question'))
    assert bus.journal(owner, workflow.id) == []
    store.db.execute('DROP TRIGGER fail_message_outbox')
    question, = bus.send(host, first.id, first.fence, draft(second, kind='question'))
    answer, = bus.send(host, second.id, second.fence, draft(first, key='answer', kind='answer', causation_id=question.id))
    store.db.execute("CREATE TRIGGER fail_receipt_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        bus.acknowledge(host, first.id, first.fence, answer.id)
    assert store.db.execute('SELECT COUNT(*) FROM message_inbox').fetchone()[0] == 0
    assert store.db.execute('SELECT answer_id FROM message_questions').fetchone()[0] is None
    store.db.execute('DROP TRIGGER fail_receipt_outbox')
    assert bus.pending(host, first.id, first.fence) == [answer]
    assert bus.acknowledge(host, first.id, first.fence, answer.id).outcome == 'received'


def test_successful_actual_inference_consumes_labeled_addressed_context(peers, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.messages import Messages

    store, _, host, _, first, second, _, _ = peers
    bus = Messages(store)
    message, = bus.send(host, first.id, first.fence, draft(second, text='The parser boundary requires inspection.'))
    cortex = AttemptCortex(store, host, second.id, second.fence)
    candidates = cortex.generate_candidates(CortexContext(task='Inspect.', behavior_mode='explore',
        observation=Observation(task='Inspect.')))
    assert candidates
    request = model_server.requests[-1]
    prompt = request['messages'][-1]['content']
    assert 'The parser boundary requires inspection.' in prompt
    assert 'untrusted data' in prompt and message.id in prompt and first.instance_id in prompt
    assert bus.pending(host, second.id, second.fence) == []
    assert bus.acknowledge(host, second.id, second.fence, message.id).outcome == 'received'


@pytest.mark.parametrize('condition', ['http_failure', 'local_verification', 'oversized_context'])
def test_failed_or_local_inference_does_not_ack_unconsumed_messages(peers, model_server, condition):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.messages import Messages

    store, _, host, _, first, second, _, _ = peers
    bus = Messages(store)
    text = 'x' * 11000 if condition == 'oversized_context' else 'Inspect the parser boundary.'
    message, = bus.send(host, first.id, first.fence, draft(second, text=text))
    if condition == 'http_failure':
        model_server.respond = lambda request: (400, {'error': {'message': 'Rejected'}})
    cortex = AttemptCortex(store, host, second.id, second.fence)
    before = len(model_server.requests)
    cortex.generate_candidates(CortexContext(task='Inspect.',
        behavior_mode='verify' if condition == 'local_verification' else 'explore', observation=Observation(task='Inspect.')))
    assert bus.pending(host, second.id, second.fence) == [message]
    if condition == 'local_verification':
        assert len(model_server.requests) == before
    if condition == 'oversized_context':
        assert len(model_server.requests) == before + 1
        prompt = model_server.requests[-1]['messages'][-1]['content']
        assert text not in prompt and 'message context budget' in prompt


def test_peer_progress_crosses_real_worker_ipc_into_another_workers_http_context(peers, tmp_path, model_server):
    import json
    import time

    from flycode.orchestration.messages import Messages
    from flycode.orchestration.runtime import Runtime
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, first, second, source, _ = peers
    (source / 'tests/test_example.py').write_text(
        'import time\nfrom pathlib import Path\n'
        'def test_example():\n'
        '    Path("test-started").touch()\n'
        '    deadline = time.monotonic() + 20\n'
        '    while not Path("release-test").exists():\n'
        '        assert time.monotonic() < deadline\n'
        '        time.sleep(.02)\n')
    original = (source / 'tests/test_example.py').read_bytes()
    def response(request):
        tools = request['response_format']['json_schema']['schema']['properties']['candidates']['items']['properties']['tool']['enum']
        candidate = {'tool': 'run_tests'} if 'run_tests' in tools else {
            'tool': 'read_file', 'arguments': {'path': 'src/example.py'}}
        return model_server.completion(json.dumps({'candidates': [candidate]}))
    model_server.respond = response
    runtime = Runtime(store, tmp_path / 'runtime')
    sender = runtime.launch(host, first.id, first.fence, source, brain='heuristic_fsm',
        message_recipients=(second.instance_id,))
    receiver = None
    copy = runtime.workspaces.repository(owner, first.id)
    try:
        deadline = time.monotonic() + 12
        while not (copy / 'test-started').exists():
            assert time.monotonic() < deadline, sender.error
            time.sleep(.02)
        sent, = Messages(store).journal(owner, workflow.id)
        assert sent.attempt_id == first.id and sent.content.kind == 'progress'
        assert 'Controller entered' in sent.content.summary
        receiver = runtime.launch(host, second.id, second.fence, source, brain='heuristic_fsm')
        deadline = time.monotonic() + 10
        while store.db.execute('SELECT COUNT(*) FROM message_inbox').fetchone()[0] == 0:
            assert time.monotonic() < deadline, receiver.error
            time.sleep(.02)
        receipt = Messages(store).acknowledge(host, second.id, second.fence, sent.id)
        assert receipt.outcome == 'received'
        assert any(sent.id in item['messages'][-1]['content'] and first.instance_id in item['messages'][-1]['content']
                   for item in model_server.requests)
        assert sender.process.poll() is None
        (copy / 'release-test').touch()
        assert sender.finished.wait(12) and receiver.finished.wait(12)
        assert any(call.attempt_id == first.id and call.tool == 'run_tests' and call.status == 'completed'
                   for call in Tools(store).calls(owner, workflow.id))
        assert (source / 'tests/test_example.py').read_bytes() == original
        assert not (source / 'test-started').exists()
        assert store.task(owner, first.task_id).state != 'succeeded'
    finally:
        (copy / 'release-test').touch()
        sender.close()
        assert sender.finished.wait(10)
        if receiver is not None:
            receiver.close()
            assert receiver.finished.wait(10)


def test_large_pending_message_does_not_starve_later_small_messages(peers, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.messages import Messages

    store, _, host, _, first, second, _, _ = peers
    bus = Messages(store)
    large, = bus.send(host, first.id, first.fence, draft(second, text='x' * 11000))
    small, = bus.send(host, first.id, first.fence, draft(second, key='small', text='Check the parser boundary.'))
    cortex = AttemptCortex(store, host, second.id, second.fence)
    cortex.generate_candidates(CortexContext(task='Inspect.', behavior_mode='explore', observation=Observation(task='Inspect.')))
    prompt = model_server.requests[-1]['messages'][-1]['content']
    assert small.id in prompt and 'Check the parser boundary.' in prompt
    assert 'message context budget' in prompt
    assert bus.pending(host, second.id, second.fence) == [large]


@pytest.mark.parametrize('condition', ['peer_stopped', 'message_budget'])
def test_blocked_progress_does_not_abort_agent_or_refund_execution_budget(peers, tmp_path, monkeypatch, condition):
    from flycode.orchestration.messages import Messages
    from flycode.orchestration.runtime import Runtime

    store, owner, host, workflow, first, second, source, _ = peers
    runtime = Runtime(store, tmp_path / 'runtime')
    if condition == 'message_budget':
        for index in range(200):
            Messages(store).send(host, first.id, first.fence, draft(second, key=str(index)))
    else:
        prepare = runtime.workspaces.prepare
        def stop_peer_after_route_validation(*args, **kwargs):
            workspace = prepare(*args, **kwargs)
            current = store.task(owner, second.task_id)
            store.command(owner, current.id, TaskCommand(operation='cancel', expected_revision=current.revision,
                idempotency_key='stop-peer'))
            store.confirm_stopped(host, second.id, second.fence, outcome='cancelled', expenditure_confirmed=True)
            return workspace
        monkeypatch.setattr(runtime.workspaces, 'prepare', stop_peer_after_route_validation)
    handle = runtime.launch(host, first.id, first.fence, source, brain='heuristic_fsm',
        message_recipients=(second.instance_id,))
    try:
        assert handle.finished.wait(15)
        assert runtime.worker(owner, first.id).result_hash is not None
        assert store.budget(owner, workflow.id).spent.model_calls > 0
        expected = 'recipient' if condition == 'peer_stopped' else 'message_budget'
        assert any(event.kind == 'message_blocked' and event.data['reason'] == expected
                   for event in store.events(owner, workflow.id))
    finally:
        handle.close()


def test_host_restart_identity_does_not_duplicate_an_attempts_idempotent_send(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    bus = Messages(store)
    original = bus.send(host, first.id, first.fence, draft(second))
    restarted_host = host.model_copy(update={'actor_id': 'restarted-host'})
    assert bus.send(restarted_host, first.id, first.fence, draft(second)) == original
    assert len(bus.journal(owner, workflow.id)) == 1


def test_two_connections_receiving_competing_answers_choose_one_current_answer(peers):
    from flycode.orchestration.messages import Messages

    store, _, host, _, first, second, _, _ = peers
    bus = Messages(store)
    question, = bus.send(host, first.id, first.fence, draft(second, kind='question'))
    answers = [bus.send(host, second.id, second.fence, draft(first, key=f'answer-{index}', kind='answer',
        text=f'Answer {index}', causation_id=question.id))[0] for index in range(2)]
    barrier = Barrier(2)
    def receive(message):
        connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
        try:
            barrier.wait(timeout=3)
            return Messages(connection).acknowledge(host, first.id, first.fence, message.id)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(receive, answers))
    assert sorted(item.outcome for item in receipts) == ['audit', 'received']
    winner, = [item.message_id for item in receipts if item.outcome == 'received']
    assert store.db.execute('SELECT answer_id FROM message_questions').fetchone()[0] == winner
    assert store.db.execute('SELECT COUNT(*) FROM message_inbox').fetchone()[0] == 2


def test_schema5_upgrade_retains_assignment_and_spending_while_adding_messages(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    store.spend(host, first.id, first.fence, BudgetAmount(model_calls=1), key='known-expense')
    for table in ('message_questions', 'message_inbox', 'message_outbox', 'message_batches', 'messages'):
        store.db.execute(f'DROP TABLE {table}')
    store.db.execute('PRAGMA user_version=5')
    connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
    try:
        assert connection.budget(owner, workflow.id).spent.model_calls == 1
        assert connection.task(owner, first.task_id).current_attempt_id == first.id
        message, = Messages(connection).send(host, first.id, first.fence, draft(second))
        assert Messages(connection).pending(host, second.id, second.fence) == [message]
        assert connection.budget(owner, workflow.id).spent.model_calls == 1
    finally:
        connection.close()


def test_metadata_revision_keeps_delivery_and_contract_edit_requires_stopped_sender(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, _, first, second, _, _ = peers
    bus = Messages(store)
    message, = bus.send(host, second.id, second.fence, draft(first))
    current = store.task(owner, second.task_id)
    current = store.command(owner, current.id, TaskCommand(operation='set_metadata', expected_revision=current.revision,
        idempotency_key='rename-source', changes={'title': 'Inspection renamed'}))
    assert bus.pending(host, first.id, first.fence) == [message]
    with pytest.raises(StoreError, match='ownership'):
        store.command(owner, current.id, TaskCommand(operation='revise', expected_revision=current.revision,
            idempotency_key='change-objective', changes={'objective': 'Investigate another boundary.'}))
    store.command(owner, current.id, TaskCommand(operation='pause', expected_revision=current.revision,
        idempotency_key='pause-sender'))
    assert bus.pending(host, first.id, first.fence) == []
    assert bus.acknowledge(host, first.id, first.fence, message.id).reason == 'stale_sender'


@pytest.mark.parametrize('invalid', ['unknown', 'duplicate', 'fanout', 'read_scope'])
def test_invalid_worker_message_route_is_rejected_before_copy_or_launch(peers, tmp_path, invalid):
    from pydantic import ValidationError

    from flycode.orchestration.runtime import Runtime

    store, owner, host, workflow, first, second, source, _ = peers
    routes = {'unknown': ('unknown-instance',), 'duplicate': (second.instance_id,) * 2,
              'fanout': ('one', 'two', 'three', 'four')}
    if invalid == 'read_scope':
        task = store.task(owner, second.task_id)
        narrow = add_peer(store, owner, host, workflow, first,
            permissions=task.permissions.model_copy(update={'read_scopes': ('src',)}))
        routes[invalid] = (narrow.instance_id,)
    runtime = Runtime(store, tmp_path / 'runtime')
    with pytest.raises((StoreError, ValidationError)):
        runtime.launch(host, first.id, first.fence, source, message_recipients=routes[invalid])
    assert not (tmp_path / 'runtime').exists()
    assert store.db.execute('SELECT COUNT(*) FROM workers').fetchone()[0] == 0


def test_answer_to_old_question_is_not_retargeted_to_a_replacement_attempt(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, _, first, second, _, _ = peers
    bus = Messages(store)
    question, = bus.send(host, second.id, second.fence, draft(first, kind='question'))
    bus.acknowledge(host, first.id, first.fence, question.id)
    current = store.task(owner, second.task_id)
    store.command(owner, current.id, TaskCommand(operation='release_assignment', expected_revision=current.revision,
        idempotency_key='release-old-session'))
    store.confirm_stopped(host, second.id, second.fence, outcome='retry_wait', expenditure_confirmed=True)
    current = store.task(owner, second.task_id)
    current = store.command(owner, current.id, TaskCommand(operation='retry', expected_revision=current.revision,
        idempotency_key='retry-session'))
    instance = store.instance(host, second.instance_id)
    replacement = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=current.id, expected_task_revision=current.revision, reserve=BudgetAmount(model_calls=2, tool_calls=3),
        idempotency_key='new-session')).attempt
    assert replacement is not None
    replacement = store.confirm_started(host, replacement.id, replacement.fence)
    answer, = bus.send(host, first.id, first.fence, draft(replacement, key='late-answer', kind='answer',
        text='Answer for the old writer.', causation_id=question.id))
    assert answer.recipient_attempt_id == second.id
    assert bus.pending(host, replacement.id, replacement.fence) == []
    with pytest.raises(StoreError, match='recipient'):
        bus.acknowledge(host, replacement.id, replacement.fence, answer.id)
    receipt = bus.acknowledge(host, second.id, second.fence, answer.id)
    assert receipt.outcome == 'audit' and receipt.reason == 'stale_recipient'


def test_stale_first_broadcast_target_does_not_disable_another_targets_question(peers):
    from flycode.domain.orchestration import MessageDraft
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    third = add_peer(store, owner, host, workflow, first)
    current = store.task(owner, second.task_id)
    store.command(owner, current.id, TaskCommand(operation='pause', expected_revision=current.revision,
        idempotency_key='pause-first-target'))
    bus = Messages(store)
    sent = bus.send(host, first.id, first.fence, MessageDraft(recipients=(second.instance_id, third.instance_id),
        content={'kind': 'question', 'question': 'Which boundary should be tested?'}, idempotency_key='broadcast-question'))
    question, = bus.pending(host, third.id, third.fence)
    assert question in sent
    answer, = bus.send(host, third.id, third.fence, draft(first, key='answer', kind='answer', causation_id=question.id))
    assert bus.acknowledge(host, first.id, first.fence, answer.id).outcome == 'received'


def test_progress_that_exceeds_remaining_context_cannot_stop_baseline_inference(peers, model_server):
    import json

    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    cortex = AttemptCortex(store, host, second.id, second.fence)
    context = CortexContext(task='Inspect.', behavior_mode='explore', observation=Observation(task='Inspect.'), working_memory=[''])
    assert cortex.generate_candidates(context)
    request = model_server.requests[-1]
    padding = 8192 - request['max_tokens'] - len(json.dumps(request['messages'], ensure_ascii=False).encode()) - 100
    assert padding > 0
    context = context.model_copy(update={'working_memory': ['X' * padding]})
    bus = Messages(store)
    message, = bus.send(host, first.id, first.fence, draft(second))
    before = len(model_server.requests)
    assert cortex.generate_candidates(context)
    assert len(model_server.requests) == before + 1
    assert bus.pending(host, second.id, second.fence) == [message]
    assert store.budget(owner, workflow.id).spent.model_calls == 2


def test_late_answer_remains_audit_evidence_after_its_old_contract_changes(peers):
    from flycode.domain.orchestration import MessageArtifact
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    narrow = store.task(owner, second.task_id).permissions.model_copy(update={'read_scopes': ('src',)})
    inputs = {'a' * 32: 'b' * 64}
    third = add_peer(store, owner, host, workflow, first, permissions=narrow, input_artifacts=inputs)
    fourth = add_peer(store, owner, host, workflow, first, permissions=narrow, input_artifacts=inputs)
    bus = Messages(store)
    question, = bus.send(host, third.id, third.fence, draft(fourth, kind='question'))
    current = store.task(owner, fourth.task_id)
    store.command(owner, current.id, TaskCommand(operation='release_assignment', expected_revision=current.revision,
        idempotency_key='release-old-contract'))
    store.confirm_stopped(host, fourth.id, fourth.fence, outcome='retry_wait', expenditure_confirmed=True)
    current = store.task(owner, fourth.task_id)
    store.command(owner, current.id, TaskCommand(operation='revise', expected_revision=current.revision,
        idempotency_key='replace-contract', changes={'input_artifacts': {},
            'permissions': narrow.model_copy(update={'read_scopes': ('.',)}).model_dump(mode='json')}))
    answer, = bus.send(host, fourth.id, fourth.fence, draft(third, key='late-contract-answer', kind='answer',
        causation_id=question.id, artifacts=(MessageArtifact(id='a' * 32, sha256='b' * 64),)))
    assert bus.pending(host, third.id, third.fence) == []
    receipt = bus.acknowledge(host, third.id, third.fence, answer.id)
    assert receipt.outcome == 'audit' and receipt.reason == 'stale_sender'
    assert store.db.execute('SELECT answer_id FROM message_questions').fetchone()[0] is None



def test_answer_to_a_stopped_recipient_is_retained_against_the_original_attempt(peers):
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    bus = Messages(store)
    question, = bus.send(host, second.id, second.fence, draft(first, kind='question'))
    current = store.task(owner, second.task_id)
    store.command(owner, current.id, TaskCommand(operation='release_assignment', expected_revision=current.revision,
        idempotency_key='release-question-author'))
    store.confirm_stopped(host, second.id, second.fence, outcome='retry_wait', expenditure_confirmed=True)
    answer, = bus.send(host, first.id, first.fence, draft(second, key='late-to-idle', kind='answer', causation_id=question.id))
    assert answer.recipient_attempt_id == second.id
    receipt = bus.acknowledge(host, second.id, second.fence, answer.id)
    assert receipt.outcome == 'audit' and receipt.reason == 'stale_recipient'
    assert len(bus.journal(owner, workflow.id)) == 2
    assert store.db.execute('SELECT answer_id FROM message_questions').fetchone()[0] is None


def test_late_answer_keeps_obsolete_recipient_contract_links_only_for_audit(peers):
    from flycode.domain.orchestration import MessageArtifact
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, _, _, _ = peers
    inputs = {'a' * 32: 'b' * 64}
    third = add_peer(store, owner, host, workflow, first, input_artifacts=inputs)
    fourth = add_peer(store, owner, host, workflow, first, input_artifacts=inputs)
    bus = Messages(store)
    question, = bus.send(host, third.id, third.fence, draft(fourth, kind='question'))
    current = store.task(owner, third.task_id)
    store.command(owner, current.id, TaskCommand(operation='release_assignment', expected_revision=current.revision,
        idempotency_key='release-recipient-contract'))
    store.confirm_stopped(host, third.id, third.fence, outcome='retry_wait', expenditure_confirmed=True)
    current = store.task(owner, third.task_id)
    store.command(owner, current.id, TaskCommand(operation='revise', expected_revision=current.revision,
        idempotency_key='replace-recipient-contract', changes={'input_artifacts': {},
            'permissions': current.permissions.model_copy(update={'read_scopes': ('src',)}).model_dump(mode='json')}))
    answer, = bus.send(host, fourth.id, fourth.fence, draft(third, key='late-recipient-contract', kind='answer',
        causation_id=question.id, artifacts=(MessageArtifact(id='a' * 32, sha256='b' * 64),)))
    receipt = bus.acknowledge(host, third.id, third.fence, answer.id)
    assert receipt.outcome == 'audit' and receipt.reason == 'stale_recipient'
    assert store.db.execute('SELECT answer_id FROM message_questions').fetchone()[0] is None


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
def test_selected_message_tool_debits_before_publication_and_cannot_replay(peers):
    from flycode.domain import ToolAction
    from flycode.orchestration.messages import Messages
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, first, second, source, _ = peers
    action = ToolAction(id='selected-question', tool='send_message', arguments={
        'recipients': [second.instance_id], 'content': {'kind': 'question', 'question': 'Which boundary needs a test?'}})
    bus = Messages(store)
    result = bus.execute(host, first.id, first.fence, root=source, action=action, routes=(second.instance_id,))
    assert result.success
    sent, = bus.journal(owner, workflow.id)
    assert result.payload == {'message_ids': [sent.id]}
    assert sent.content.kind == 'question' and sent.sender_id == first.instance_id
    assert sent.idempotency_key == 'tool:selected-question'
    assert store.budget(owner, workflow.id).spent.tool_calls == 1
    call, = Tools(store).calls(owner, workflow.id)
    assert call.tool == 'send_message' and call.status == 'completed'
    events = store.events(owner, workflow.id)
    assert [item.kind for item in events if item.kind in {'tool_start', 'message_sent', 'tool_end'}] == [
        'tool_start', 'message_sent', 'tool_end']
    assert not bus.execute(host, first.id, first.fence, root=source, action=action,
        routes=(second.instance_id,)).success
    assert len(bus.journal(owner, workflow.id)) == 1
    assert store.budget(owner, workflow.id).spent.tool_calls == 1
    assert store.task(owner, first.task_id).state == 'running'
    assert (source / 'src/example.py').read_text() == 'answer = 0\n'


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
@pytest.mark.parametrize('routes', [False, True])
def test_actual_model_schema_exposes_communication_only_with_host_routes(peers, model_server, routes):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, _, host, _, first, second, _, _ = peers
    cortex = AttemptCortex(store, host, first.id, first.fence,
        message_recipients=(second.instance_id,) if routes else ())
    cortex.generate_candidates(CortexContext(task='Inspect.', behavior_mode='explore', observation=Observation(task='Inspect.')))
    request = model_server.requests[-1]
    tools = request['response_format']['json_schema']['schema']['properties']['candidates']['items']['properties']['tool']['enum']
    assert ('send_message' in tools) == routes
    prompt = request['messages'][-1]['content']
    assert (second.instance_id in prompt) == routes
    if routes:
        assert 'causation_id' in prompt and 'question' in prompt and 'answer' in prompt


def test_addressed_question_is_not_starved_by_earlier_controller_progress(peers, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.messages import Messages

    store, _, host, _, first, second, _, _ = peers
    bus = Messages(store)
    progress, = bus.send(host, first.id, first.fence, draft(second, key='progress', text='Controller entered explore at step 1.'))
    question, = bus.send(host, first.id, first.fence, draft(second, key='question', kind='question',
        text='Which boundary should receive a regression test?'))
    AttemptCortex(store, host, second.id, second.fence).generate_candidates(CortexContext(
        task='Inspect.', behavior_mode='explore', observation=Observation(task='Inspect.')))
    prompt = model_server.requests[-1]['messages'][-1]['content']
    assert 'Which boundary should receive a regression test?' in prompt
    assert question.id in prompt and bus.pending(host, second.id, second.fence) == [progress]


def test_received_question_remains_available_for_a_later_model_decision(peers, model_server):
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, _ = peers
    bus = Messages(store)
    question, = bus.send(host, first.id, first.fence, draft(second, kind='question', text='Which empty boundary should we verify?'))
    context = CortexContext(task='Inspect.', behavior_mode='explore', observation=Observation(task='Inspect.'))
    cortex = AttemptCortex(store, host, second.id, second.fence)
    assert cortex.generate_candidates(context)
    assert not bus.pending(host, second.id, second.fence)
    assert cortex.generate_candidates(context)
    prompt = model_server.requests[-1]['messages'][-1]['content']
    assert 'Which empty boundary should we verify?' in prompt and question.id in prompt
    assert sum(item.kind == 'message_received' for item in store.events(owner, workflow.id)) == 1


def test_remembered_context_keeps_only_accepted_winning_answer_and_closes_question(peers):
    from flycode.orchestration.messages import Messages

    store, _, host, _, first, second, _, _ = peers
    bus = Messages(store)
    question, = bus.send(host, first.id, first.fence, draft(second, kind='question'))
    bus.acknowledge(host, second.id, second.fence, question.id)
    assert bus.remembered(host, second.id, second.fence) == [question]
    winner, = bus.send(host, second.id, second.fence, draft(first, key='winner', kind='answer', causation_id=question.id))
    other, = bus.send(host, second.id, second.fence, draft(first, key='other', kind='answer', causation_id=question.id))
    assert bus.acknowledge(host, first.id, first.fence, winner.id).outcome == 'received'
    assert bus.acknowledge(host, first.id, first.fence, other.id).outcome == 'audit'
    assert bus.remembered(host, first.id, first.fence) == [winner]
    assert not bus.remembered(host, second.id, second.fence)


@pytest.mark.parametrize('condition', ['expired', 'revoked_sender', 'revoked_recipient'])
def test_remembered_context_cannot_reintroduce_expired_or_revoked_messages(peers, condition):
    from flycode.orchestration.messages import Messages

    store, owner, host, _, first, second, _, now = peers
    bus = Messages(store)
    question, = bus.send(host, second.id, second.fence, draft(first, kind='question', ttl_seconds=1))
    bus.acknowledge(host, first.id, first.fence, question.id)
    if condition == 'expired':
        now[0] += 2
    else:
        task = store.task(owner, second.task_id if condition == 'revoked_sender' else first.task_id)
        store.command(owner, task.id, TaskCommand(operation='pause', expected_revision=task.revision,
            idempotency_key='revoke-remembered'))
    if condition == 'revoked_recipient':
        with pytest.raises(StoreError, match='fence'):
            bus.remembered(host, first.id, first.fence)
    else:
        assert not bus.remembered(host, first.id, first.fence)


@pytest.mark.parametrize('received', [False, True])
@pytest.mark.parametrize('condition', ['expired', 'revoked_sender'])
def test_context_invalidated_during_actual_http_cannot_publish_candidates(peers, model_server, received, condition):
    from threading import Event

    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, _, now = peers
    bus = Messages(store)
    question, = bus.send(host, second.id, second.fence, draft(first, kind='question', ttl_seconds=1,
        text='Inspect the empty boundary before editing.'))
    if received:
        bus.acknowledge(host, first.id, first.fence, question.id)
    entered, release = Event(), Event()
    def response(request):
        assert question.id in request['messages'][-1]['content']
        assert 'Inspect the empty boundary before editing.' in request['messages'][-1]['content']
        entered.set()
        assert release.wait(5)
        return model_server.completion('{"candidates":[{"tool":"read_file","arguments":{"path":"src/example.py"}}]}')
    model_server.respond = response
    def infer():
        connection = Store(store.path, catalogue=store.catalogue, clock=store.clock)
        try:
            cortex = AttemptCortex(connection, host, first.id, first.fence)
            candidates = cortex.generate_candidates(CortexContext(task='Inspect.', behavior_mode='explore',
                observation=Observation(task='Inspect.')))
            return candidates, cortex.last_routing.error
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(infer)
        try:
            assert entered.wait(5)
            if condition == 'expired':
                now[0] += 2
            else:
                task = store.task(owner, second.task_id)
                store.command(owner, task.id, TaskCommand(operation='pause', expected_revision=task.revision,
                    idempotency_key='revoke-during-http'))
        finally:
            release.set()
        candidates, error = future.result(timeout=5)
    assert candidates == [] and error == 'message_context_stale'
    assert store.budget(owner, workflow.id).spent.model_calls == 1
    assert store._attempt(first.id).state == 'running'
    assert any(item.kind == 'message_context_invalidated' for item in store.events(owner, workflow.id))


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
def test_communication_guidance_cannot_overflow_an_otherwise_valid_request(peers, model_server):
    import json

    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext
    from flycode.orchestration.cortex import AttemptCortex

    store, owner, host, workflow, first, second, _, _ = peers
    context = CortexContext(task='Inspect.', behavior_mode='explore', observation=Observation(task='Inspect.'), working_memory=[''])
    assert AttemptCortex(store, host, first.id, first.fence).generate_candidates(context)
    request = model_server.requests[-1]
    padding = 8192 - request['max_tokens'] - len(json.dumps(request['messages'], ensure_ascii=False).encode()) - 100
    assert padding > 0
    context = context.model_copy(update={'working_memory': ['X' * padding]})
    before = len(model_server.requests)
    candidates = AttemptCortex(store, host, first.id, first.fence,
        message_recipients=(second.instance_id,)).generate_candidates(context)
    assert candidates and len(model_server.requests) == before + 1
    tools = model_server.requests[-1]['response_format']['json_schema']['schema']['properties']['candidates']['items']['properties']['tool']['enum']
    assert 'send_message' not in tools and 'read_file' in tools
    assert store.budget(owner, workflow.id).spent.model_calls == 2
    assert any(item.kind == 'message_tool_context_deferred' for item in store.events(owner, workflow.id))


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
async def test_message_execution_uses_normal_observation_without_double_spending(peers):
    from flycode.domain import TaskSpec, ToolAction
    from flycode.orchestration.environment import AttemptEnvironment
    from flycode.orchestration.messages import Messages
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, first, second, source, _ = peers
    def execute(action):
        return Messages(store).execute(host, first.id, first.fence, root=source, action=action, routes=(second.instance_id,))
    environment = AttemptEnvironment(source, store, host, first.id, first.fence, message_executor=execute)
    before = await environment.reset(TaskSpec(id=first.task_id, task='Inspect.'))
    result = await environment.step(ToolAction(id='one-message', tool='send_message', arguments={
        'recipients': [second.instance_id], 'content': {'kind': 'progress', 'summary': 'Inspection is underway.'}}))
    assert result.success and result.current.step_number == before.step_number + 1
    assert result.current.tool_calls_used == 1 and result.tool_call_index == 1
    assert result.current.tests_status == before.tests_status and result.current.recent_errors == before.recent_errors
    assert len(Tools(store).calls(owner, workflow.id)) == 1
    assert store.budget(owner, workflow.id).spent.tool_calls == 1
    assert not environment.is_done()


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
def test_two_workers_select_question_and_answer_through_real_http_and_rpc(peers, tmp_path, model_server):
    import json
    import time

    from flycode.orchestration.messages import Messages
    from flycode.orchestration.runtime import Runtime
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, first, _, source, _ = peers
    permission = store.task(owner, first.task_id).permissions.model_copy(update={
        'write_scopes': (), 'tools': ('read_file', 'send_message')})
    second = add_peer(store, owner, host, workflow, first, permissions=permission)
    (source / 'tests/test_example.py').write_text(
        'import time\nfrom pathlib import Path\n'
        'def test_example():\n'
        '    Path("test-started").touch()\n'
        '    deadline = time.monotonic() + 20\n'
        '    while not Path("release-test").exists():\n'
        '        assert time.monotonic() < deadline\n'
        '        time.sleep(.02)\n')
    original = (source / 'tests/test_example.py').read_bytes()
    question_id = [None]
    requests = {'first': 0, 'second': 0}
    def response(request):
        tools = request['response_format']['json_schema']['schema']['properties']['candidates']['items']['properties']['tool']['enum']
        if 'run_tests' in tools:
            requests['first'] += 1
            candidate = {'tool': 'send_message', 'arguments': {'recipients': [second.instance_id],
                'content': {'kind': 'question', 'question': 'Which boundary should receive a regression test?'}}} if requests['first'] == 1 else {'tool': 'run_tests'}
        else:
            requests['second'] += 1
            if requests['second'] == 1:
                prompt = request['messages'][-1]['content']
                assert question_id[0] in prompt
                assert 'Which boundary should receive a regression test?' in prompt
                envelope, = [json.loads(line) for line in prompt.splitlines()
                    if line.startswith('{"message_id"') and json.loads(line)['content']['kind'] == 'question']
                assert envelope['sender_id'] == first.instance_id
            candidate = {'tool': 'send_message', 'arguments': {'recipients': [first.instance_id],
                'causation_id': envelope['message_id'], 'content': {'kind': 'answer', 'answer': 'Test the empty-input boundary.'}}} if requests['second'] == 1 else {
                    'tool': 'read_file', 'arguments': {'path': 'src/example.py'}}
        return model_server.completion(json.dumps({'candidates': [candidate]}))
    model_server.respond = response
    runtime = Runtime(store, tmp_path / 'runtime')
    sender = runtime.launch(host, first.id, first.fence, source, brain='heuristic_fsm', message_recipients=(second.instance_id,))
    receiver = None
    copy = runtime.workspaces.repository(owner, first.id)
    try:
        deadline = time.monotonic() + 12
        while not (copy / 'test-started').exists():
            assert time.monotonic() < deadline, sender.error
            time.sleep(.02)
        question, = [item for item in Messages(store).journal(owner, workflow.id) if item.content.kind == 'question']
        question_id[0] = question.id
        receiver = runtime.launch(host, second.id, second.fence, source, brain='heuristic_fsm', message_recipients=(first.instance_id,))
        deadline = time.monotonic() + 10
        while not any(item.content.kind == 'answer' for item in Messages(store).journal(owner, workflow.id)):
            assert time.monotonic() < deadline, receiver.error
            time.sleep(.02)
        answer, = [item for item in Messages(store).journal(owner, workflow.id) if item.content.kind == 'answer']
        assert answer.causation_id == question.id and answer.correlation_id == question.correlation_id
        assert answer.recipient_attempt_id == first.id and answer.attempt_id == second.id
        assert answer.content.answer == 'Test the empty-input boundary.'
        calls = [item for item in Tools(store).calls(owner, workflow.id) if item.tool == 'send_message']
        assert len(calls) == 2 and all(item.status == 'completed' for item in calls)
        assert sender.process.poll() is None
        (copy / 'release-test').touch()
        assert sender.finished.wait(12) and receiver.finished.wait(12)
        assert sender.error is None and receiver.error is None
        assert (source / 'tests/test_example.py').read_bytes() == original
        assert not (source / 'test-started').exists()
        assert store.task(owner, first.task_id).state != 'succeeded'
    finally:
        (copy / 'release-test').touch()
        sender.close()
        assert sender.finished.wait(10)
        if receiver is not None:
            receiver.close()
            assert receiver.finished.wait(10)


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
@pytest.mark.parametrize('change,code', [
    ({'sender_id': 'forged'}, 'invalid_message'),
    ({'idempotency_key': 'model-owned'}, 'permission'),
    ({'content': {'kind': 'question', 'question': 'Inspect?', 'permissions': {'approved': True}}}, 'invalid_message'),
    ({'content': {'kind': 'unknown', 'summary': 'Inspect.'}}, 'invalid_message'),
    ({'recipients': []}, 'invalid_message'),
    ({'ttl_seconds': 301}, 'invalid_message'),
    ({'recipients': ['unapproved-instance']}, 'message_route'),
])
def test_model_arguments_cannot_forge_host_fields_or_expand_routes(peers, change, code):
    from flycode.domain import ToolAction
    from flycode.orchestration.messages import Messages
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, first, second, source, _ = peers
    arguments = {'recipients': [second.instance_id], 'content': {'kind': 'question', 'question': 'Inspect?'}}
    arguments.update(change)
    result = Messages(store).execute(host, first.id, first.fence, root=source,
        action=ToolAction(id='invalid', tool='send_message', arguments=arguments), routes=(second.instance_id,))
    assert not result.success and result.error_message == code
    assert not Messages(store).journal(owner, workflow.id) and not Tools(store).calls(owner, workflow.id)
    assert store.budget(owner, workflow.id).spent.tool_calls == 0


def test_route_alone_cannot_grant_communication_permission(peers):
    from flycode.domain import ToolAction
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, source, _ = peers
    result = Messages(store).execute(host, first.id, first.fence, root=source,
        action=ToolAction(id='no-permission', tool='send_message', arguments={'recipients': [second.instance_id],
            'content': {'kind': 'progress', 'summary': 'Inspecting.'}}), routes=(second.instance_id,))
    assert not result.success and result.error_message == 'permission'
    assert not Messages(store).journal(owner, workflow.id)
    assert store.budget(owner, workflow.id).spent.tool_calls == 0


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
def test_broker_rejection_is_a_spent_failed_tool_and_does_not_cancel_agent(peers):
    from flycode.domain import ToolAction
    from flycode.orchestration.messages import Messages
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, first, second, source, _ = peers
    result = Messages(store).execute(host, first.id, first.fence, root=source,
        action=ToolAction(id='missing-question', tool='send_message', arguments={'recipients': [second.instance_id],
            'content': {'kind': 'answer', 'answer': 'An answer without a question.'}}), routes=(second.instance_id,))
    assert not result.success and result.error_message == 'causation'
    call, = Tools(store).calls(owner, workflow.id)
    assert call.status == 'failed' and call.error_class == 'causation'
    assert store.budget(owner, workflow.id).spent.tool_calls == 1
    assert not Messages(store).journal(owner, workflow.id)
    assert store._attempt(first.id).state == 'running'
    following, _ = Tools(store).begin(host, first.id, first.fence, root=source,
        action=ToolAction(id='next-inspection', tool='read_file', arguments={'path': 'src/example.py'}))
    assert following.status == 'running'


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
def test_message_tool_cannot_exceed_shared_tool_budget(peers):
    from flycode.domain import ToolAction
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, source, _ = peers
    bus = Messages(store)
    for number in range(3):
        assert bus.execute(host, first.id, first.fence, root=source, routes=(second.instance_id,),
            action=ToolAction(id=f'message-{number}', tool='send_message', arguments={'recipients': [second.instance_id],
                'content': {'kind': 'progress', 'summary': f'Inspection {number}.'}})).success
    with pytest.raises(StoreError, match='budget_exhausted'):
        bus.execute(host, first.id, first.fence, root=source, routes=(second.instance_id,),
            action=ToolAction(id='message-4', tool='send_message', arguments={'recipients': [second.instance_id],
                'content': {'kind': 'progress', 'summary': 'Unfunded inspection.'}}))
    assert len(bus.journal(owner, workflow.id)) == 3
    assert store.budget(owner, workflow.id).spent.tool_calls == 3


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
def test_selected_message_replay_after_host_restart_does_not_debit_twice(peers):
    from flycode.domain import ToolAction
    from flycode.orchestration.messages import Messages
    from flycode.orchestration.tools import Tools

    store, owner, host, workflow, first, second, source, _ = peers
    action = ToolAction(id='stable-action', tool='send_message', arguments={'recipients': [second.instance_id],
        'content': {'kind': 'progress', 'summary': 'One durable inspection notification.'}})
    assert Messages(store).execute(host, first.id, first.fence, root=source, action=action, routes=(second.instance_id,)).success
    restarted = Store(store.path, catalogue=store.catalogue, clock=store.clock)
    try:
        new_host = host.model_copy(update={'actor_id': 'host-after-restart'})
        result = Messages(restarted).execute(new_host, first.id, first.fence, root=source, action=action, routes=(second.instance_id,))
        assert not result.success and result.error_message == 'request_replay'
        assert len(Messages(restarted).journal(owner, workflow.id)) == 1
        assert len(Tools(restarted).calls(owner, workflow.id)) == 1
        assert restarted.budget(owner, workflow.id).spent.tool_calls == 1
    finally:
        restarted.close()


@pytest.mark.parametrize('owned', [{'tools': MESSAGE_TOOLS}], indirect=True)
@pytest.mark.parametrize('operation', ['pause', 'cancel'])
def test_revoked_attempt_cannot_publish_selected_messages(peers, operation):
    from flycode.domain import ToolAction
    from flycode.orchestration.messages import Messages

    store, owner, host, workflow, first, second, source, _ = peers
    current = store.task(owner, first.task_id)
    store.command(owner, current.id, TaskCommand(operation=operation, expected_revision=current.revision,
        idempotency_key='revoke-sender'))
    with pytest.raises(StoreError, match='fence|paused'):
        Messages(store).execute(host, first.id, first.fence, root=source, routes=(second.instance_id,),
            action=ToolAction(id='late-tool', tool='send_message', arguments={'recipients': [second.instance_id],
                'content': {'kind': 'progress', 'summary': 'Late inspection.'}}))
    assert not Messages(store).journal(owner, workflow.id)
    assert store.budget(owner, workflow.id).spent.tool_calls == 0
