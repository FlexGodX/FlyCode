"""Dynamic logical routes resolve actual approved sibling attempts."""
# ruff: noqa: F401,F811

import json

import pytest
from test_owner_command_attachment import approve_request
from test_owner_message_routes import request
from test_owner_recipe_profiles import (
    live_parent,
    managed_pipeline,
    parent_ready,
    pinned_parent,
    pipeline_ready,
    wait_handles,
)

from flycode.domain.orchestration import (
    AgentProfileDraft,
    BudgetAmount,
    ClaimRequest,
    TaskCommand,
    TaskDraft,
)
from flycode.orchestration.messages import Messages
from flycode.orchestration.owner_commands import OwnerCommands
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.store import changed


@pytest.fixture
def message_siblings(pinned_parent):
    p, store = pinned_parent, pinned_parent['store']
    owner = p['owner']
    (p['source'] / 'src/second.py').write_text('value = 0\n')
    old = store._profile(p['child'].allowed_profile_ids[0])
    permission = changed(old.permissions, tools=(*old.permissions.tools, 'send_message'))
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **old.model_dump(include=set(AgentProfileDraft.model_fields)), 'permissions': permission}))
    p['message_instances'] = [store.register_instance(p['host'], profile.id) for _ in range(2)]
    for name in ('parent', 'child'):
        task = store.task(owner, p[name].id)
        allowed = permission if name == 'parent' else changed(permission, write_scopes=('src/parser.py',))
        p[name] = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='sibling-permission-' + name, changes={'allowed_profile_ids': (profile.id,),
                'permissions': allowed.model_dump(mode='json'),
                'budget': BudgetAmount(model_calls=2 if name == 'parent' else 4,
                    tool_calls=2 if name == 'parent' else 4).model_dump()}))
    second = store.create_task(owner, p['workflow'].id, TaskDraft.model_validate({
        **p['child'].model_dump(include=set(TaskDraft.model_fields)), 'title': 'Second sibling',
        'objective': 'Ask the first sibling for its parser result and set second value.',
        'permissions': changed(permission, write_scopes=('src/second.py',))}), key='second-message-sibling')
    review = store.create_task(owner, p['workflow'].id, TaskDraft.model_validate({
        **p['child_review'].model_dump(include=set(TaskDraft.model_fields)), 'title': 'Review second sibling'}),
        key='second-message-review')
    integrator = store.task(owner, p['integrator'].id)
    integrator = store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='sibling-integrator-deps', changes={'dependencies': (*integrator.dependencies, review.id)}))
    if integrator.state == 'blocked':
        integrator = store.command(owner, integrator.id, TaskCommand(operation='reopen',
            expected_revision=integrator.revision, idempotency_key='sibling-integrator-reopen'))
    p.update(second=second, second_review=review, integrator=integrator)
    p['profile_pins'][second.id] = p['profile_pins'][p['child'].id]
    p['profile_pins'][review.id] = p['profile_pins'][p['child_review'].id]
    for name in ('parent', 'child', 'second'):
        task = store.task(owner, p[name].id)
        previous = None if name == 'second' else p['dispatcher'].binding(owner, task.id).id
        p['dispatcher'].bind(owner, task.id, p['source'], expected_revision=task.revision,
            expected_binding_id=previous, key='sibling-bind-' + name, brain='heuristic_fsm')
    command = approve_request(p)
    command['pairs'][second.id] = review.id
    approved = OwnerCommands(store).execute(command)
    assert approved.error_code is None, approved
    p['pipeline_id'] = approved.result.pipeline_id
    edges = [{'sender_task_id': p['child'].id, 'recipient_task_id': second.id},
        {'sender_task_id': second.id, 'recipient_task_id': p['child'].id}]
    grant = OwnerCommands(store).execute(request(p, edges=edges))
    assert grant.error_code is None, grant
    p['message_grant'] = grant.result.grant_id
    first = p['dispatcher'].tick(p['host'], key='message-parent-preparation')
    assert first.attempt is not None and first.attempt.task_id == p['parent'].id
    wait_handles(p['dispatcher'])
    assert Pipelines(store, p['dispatcher'].root).advance(p['host'], p['workflow'].id) == ()
    assert store.task(owner, p['parent'].id).blocked_reason == 'waiting_children'
    return p


def claim_sibling(p, name, index):
    store, host = p['store'], p['host']
    task = store.task(p['owner'], p[name].id)
    instance = store.instance(host, p['message_instances'][index].id)
    result = store.claim(host, ClaimRequest(instance_id=instance.id, expected_instance_revision=instance.revision,
        task_id=task.id, expected_task_revision=task.revision, reserve=task.budget, idempotency_key='claim-message-' + name))
    assert result.attempt is not None, result.reasons
    return store.confirm_started(host, result.attempt.id, result.attempt.fence)


def test_approved_future_peer_appears_without_restarting_sender(message_siblings):
    p = message_siblings
    first = claim_sibling(p, 'child', 0)
    bus = Messages(p['store'])
    before = bus.routing(p['host'], first.id, first.fence)
    assert before.endpoints == () and before.grant.id == p['message_grant']
    second = claim_sibling(p, 'second', 1)
    after = bus.routing(p['host'], first.id, first.fence)
    assert [pin.instance_id for pin in after.endpoints] == [second.instance_id]
    assert after.endpoints[0].attempt_id == second.id and after.grant == before.grant
    assert p['store']._attempt(first.id).state == 'running'


def context():
    from flycode.domain import Observation
    from flycode.llm.protocol import CortexContext

    return CortexContext(task='Inspect the assigned source.', behavior_mode='explore',
        observation=Observation(task='Inspect the assigned source.'))


def selected_question(p, model_server, first, second):
    from flycode.orchestration.cortex import AttemptCortex

    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
        'tool': 'send_message', 'arguments': {'recipients': [second.instance_id],
            'content': {'kind': 'question', 'question': 'Which parser result should I use?'}}}]}))
    cortex = AttemptCortex(p['store'], p['host'], first.id, first.fence)
    actions = cortex.generate_candidates(context())
    assert len(actions) == 1 and actions[0].tool == 'send_message', cortex.last_routing
    return cortex, actions[0]


def test_actual_http_discovers_future_peer_and_uses_owner_route(message_siblings, model_server):
    from flycode.orchestration.cortex import AttemptCortex

    p, store = message_siblings, message_siblings['store']
    first = claim_sibling(p, 'child', 0)
    cortex = AttemptCortex(store, p['host'], first.id, first.fence)
    try:
        model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
            'tool': 'read_file', 'arguments': {'path': 'src/parser.py'}}]}))
        assert cortex.generate_candidates(context())
        schema = model_server.requests[-1]['response_format']['json_schema']['schema']
        assert 'send_message' not in schema['properties']['candidates']['items']['properties']['tool']['enum']
        second = claim_sibling(p, 'second', 1)
        model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
            'tool': 'send_message', 'arguments': {'recipients': [second.instance_id],
                'content': {'kind': 'question', 'question': 'Which result is ready?'}}}]}))
        action, = cortex.generate_candidates(context())
        prompt = model_server.requests[-1]['messages'][-1]['content']
        assert second.id in prompt and second.instance_id in prompt and second.contract_hash in prompt
        result = cortex.execute_message(p['source'], action)
        assert result.success, result
        message, = Messages(store).pending(p['host'], second.id, second.fence)
        assert message.attempt_id == first.id and message.recipient_attempt_id == second.id
        assert store.db.execute('SELECT grant_id FROM message_route_proofs WHERE message_id=?', (message.id,)).fetchone()[0] == p['message_grant']
    finally:
        cortex.core.reasoner._client.close()


def test_owner_revoke_during_actual_http_retains_usage_without_candidates(message_siblings, model_server):
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.store import Store

    p, store = message_siblings, message_siblings['store']
    first, second = claim_sibling(p, 'child', 0), claim_sibling(p, 'second', 1)
    revoke = request(p, key='revoke-held-http', head=p['message_grant'], edges=[])
    before = store.budget(p['owner'], p['workflow'].id).spent
    entered = []

    def respond(payload):
        assert second.instance_id in payload['messages'][-1]['content']
        connection = Store(store.path, host=store.host)
        try:
            # A real owner write during HTTP also proves inference owns no writer transaction.
            assert OwnerCommands(connection).execute(revoke).error_code is None
            entered.append(True)
        finally:
            connection.close()
        return model_server.completion(json.dumps({'candidates': [{'tool': 'read_file',
            'arguments': {'path': 'src/parser.py'}}]}))

    model_server.respond = respond
    cortex = AttemptCortex(store, p['host'], first.id, first.fence)
    try:
        assert cortex.generate_candidates(context()) == []
        assert entered and cortex.last_routing.error == 'message_route_stale'
        after = store.budget(p['owner'], p['workflow'].id).spent
        assert after.model_calls == before.model_calls + 1 and after.tool_calls == before.tool_calls
        assert not Messages(store).journal(p['owner'], p['workflow'].id)
    finally:
        cortex.core.reasoner._client.close()


def test_revoke_after_tool_admission_spends_once_without_delivery(message_siblings, model_server, monkeypatch):
    from flycode.orchestration.tools import Tools

    p, store = message_siblings, message_siblings['store']
    first, second = claim_sibling(p, 'child', 0), claim_sibling(p, 'second', 1)
    cortex, action = selected_question(p, model_server, first, second)
    original = Tools.begin
    before = store.budget(p['owner'], p['workflow'].id).spent

    def admitted(service, *args, **kwargs):
        result = original(service, *args, **kwargs)
        assert OwnerCommands(store).execute(request(p, key='revoke-admitted', head=p['message_grant'], edges=[])).error_code is None
        return result

    monkeypatch.setattr(Tools, 'begin', admitted)
    try:
        result = cortex.execute_message(p['source'], action)
        assert not result.success and result.error_message == 'message_route_stale'
        assert not Messages(store).journal(p['owner'], p['workflow'].id)
        after = store.budget(p['owner'], p['workflow'].id).spent
        assert after.tool_calls == before.tool_calls + 1
        calls = store.db.execute('SELECT data FROM tool_calls WHERE attempt_id=?', (first.id,)).fetchall()
        assert len(calls) == 1 and json.loads(calls[0]['data'])['status'] == 'failed'
    finally:
        cortex.core.reasoner._client.close()


@pytest.mark.parametrize('received', [False, True])
def test_revoke_excludes_pending_and_remembered_question(message_siblings, model_server, received):
    p, store = message_siblings, message_siblings['store']
    first, second = claim_sibling(p, 'child', 0), claim_sibling(p, 'second', 1)
    cortex, action = selected_question(p, model_server, first, second)
    bus = Messages(store)
    try:
        assert cortex.execute_message(p['source'], action).success
        message, = bus.pending(p['host'], second.id, second.fence)
        if received:
            assert bus.acknowledge(p['host'], second.id, second.fence, message.id).outcome == 'received'
            assert bus.remembered(p['host'], second.id, second.fence) == [message]
        assert OwnerCommands(store).execute(request(p, key='revoke-pending', head=p['message_grant'], edges=[])).error_code is None
        assert bus.pending(p['host'], second.id, second.fence) == []
        assert bus.remembered(p['host'], second.id, second.fence) == []
        assert bus.journal(p['owner'], p['workflow'].id) == [message]
        if not received:
            receipt = bus.acknowledge(p['host'], second.id, second.fence, message.id)
            assert receipt.outcome == 'audit' and receipt.reason == 'message_route_revoked'
    finally:
        cortex.core.reasoner._client.close()


@pytest.mark.parametrize('corruption', ['delete', 'substitute'])
def test_changing_delivery_proof_cannot_restore_revoked_question(message_siblings, model_server, corruption):
    p, store = message_siblings, message_siblings['store']
    first, second = claim_sibling(p, 'child', 0), claim_sibling(p, 'second', 1)
    cortex, action = selected_question(p, model_server, first, second)
    bus = Messages(store)
    try:
        assert cortex.execute_message(p['source'], action).success
        message, = bus.pending(p['host'], second.id, second.fence)
        revoked = OwnerCommands(store).execute(request(p, key='revoke-before-proof-loss', head=p['message_grant'], edges=[]))
        assert revoked.error_code is None
        if corruption == 'delete':
            store.db.execute('DELETE FROM message_route_proofs WHERE message_id=?', (message.id,))
        else:
            edges = [{'sender_task_id': p['child'].id, 'recipient_task_id': second.task_id},
                {'sender_task_id': second.task_id, 'recipient_task_id': p['child'].id}]
            newer = OwnerCommands(store).execute(request(p, key='regrant-before-proof-substitution',
                head=revoked.result.grant_id, edges=edges))
            assert newer.error_code is None
            proof = store.db.execute("SELECT json_extract(data,'$.data.grant_hash') FROM events "
                "WHERE json_extract(data,'$.data.grant_id')=?", (newer.result.grant_id,)).fetchone()[0]
            store.db.execute('UPDATE message_route_proofs SET grant_id=?,grant_hash=? WHERE message_id=?',
                (newer.result.grant_id, proof, message.id))
        assert bus.pending(p['host'], second.id, second.fence) == []
        assert bus.acknowledge(p['host'], second.id, second.fence, message.id).reason == 'message_route_revoked'
    finally:
        cortex.core.reasoner._client.close()


def test_question_answer_cross_two_actual_http_contexts(message_siblings, model_server):
    from flycode.orchestration.cortex import AttemptCortex

    p, store = message_siblings, message_siblings['store']
    first, second = claim_sibling(p, 'child', 0), claim_sibling(p, 'second', 1)
    cortex, action = selected_question(p, model_server, first, second)
    answerer = AttemptCortex(store, p['host'], second.id, second.fence)
    bus = Messages(store)
    try:
        assert cortex.execute_message(p['source'], action).success
        question, = bus.pending(p['host'], second.id, second.fence)

        def respond(payload):
            prompt = payload['messages'][-1]['content']
            assert question.id in prompt and 'Which parser result should I use?' in prompt
            return model_server.completion(json.dumps({'candidates': [{'tool': 'send_message', 'arguments': {
                'recipients': [first.instance_id], 'causation_id': question.id,
                'content': {'kind': 'answer', 'answer': 'Use parser result 1.'}}}]}))

        model_server.respond = respond
        reply, = answerer.generate_candidates(context())
        assert answerer.execute_message(p['source'], reply).success
        answer, = bus.pending(p['host'], first.id, first.fence)
        assert answer.causation_id == question.id and answer.correlation_id == question.correlation_id
        model_server.respond = lambda payload: model_server.completion(json.dumps({'candidates': [{
            'tool': 'read_file', 'arguments': {'path': 'src/parser.py'}}]}))
        assert cortex.generate_candidates(context())
        prompt = model_server.requests[-1]['messages'][-1]['content']
        assert answer.id in prompt and 'Use parser result 1.' in prompt
        assert bus.pending(p['host'], first.id, first.fence) == []
        assert bus.acknowledge(p['host'], first.id, first.fence, answer.id).outcome == 'received'
        assert len(bus.journal(p['owner'], p['workflow'].id)) == 2
    finally:
        answerer.core.reasoner._client.close()
        cortex.core.reasoner._client.close()


def test_regrant_cannot_answer_a_question_from_revoked_authority(message_siblings, model_server):
    from flycode.orchestration.cortex import AttemptCortex

    p, store = message_siblings, message_siblings['store']
    first, second = claim_sibling(p, 'child', 0), claim_sibling(p, 'second', 1)
    cortex, action = selected_question(p, model_server, first, second)
    answerer = AttemptCortex(store, p['host'], second.id, second.fence)
    bus = Messages(store)
    try:
        assert cortex.execute_message(p['source'], action).success
        question, = bus.pending(p['host'], second.id, second.fence)
        assert bus.acknowledge(p['host'], second.id, second.fence, question.id).outcome == 'received'
        revoked = OwnerCommands(store).execute(request(p, key='revoke-before-new-answer', head=p['message_grant'], edges=[]))
        assert revoked.error_code is None
        newer = OwnerCommands(store).execute(request(p, key='regrant-before-new-answer', head=revoked.result.grant_id,
            edges=[{'sender_task_id': first.task_id, 'recipient_task_id': second.task_id},
                {'sender_task_id': second.task_id, 'recipient_task_id': first.task_id}]))
        assert newer.error_code is None
        model_server.respond = lambda payload: model_server.completion(json.dumps({'candidates': [{
            'tool': 'send_message', 'arguments': {'recipients': [first.instance_id], 'causation_id': question.id,
                'content': {'kind': 'answer', 'answer': 'This uses an old question.'}}}]}))
        reply, = answerer.generate_candidates(context())
        assert question.id not in model_server.requests[-1]['messages'][-1]['content']
        result = answerer.execute_message(p['source'], reply)
        assert not result.success and result.error_message == 'message_route_stale', result
        assert bus.journal(p['owner'], p['workflow'].id) == [question]
    finally:
        answerer.core.reasoner._client.close()
        cortex.core.reasoner._client.close()


def test_stale_graph_hides_routes_without_blocking_new_approved_work(message_siblings, model_server):
    from flycode.orchestration.cortex import AttemptCortex

    p, store = message_siblings, message_siblings['store']
    payload = approve_request(p)
    payload.pop('workflow_id')
    payload.update(operation='replan', pipeline_id=p['pipeline_id'], idempotency_key='revise-before-new-worker',
        changes={p['child'].id: {'objective': 'Inspect the revised parser boundary.'}})
    for field in ('pairs', 'integrator_id'):
        payload.pop(field)
    outcome = OwnerCommands(store).execute(payload)
    assert outcome.error_code is None, outcome
    first = claim_sibling(p, 'child', 0)
    claim_sibling(p, 'second', 1)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
        'tool': 'read_file', 'arguments': {'path': 'src/parser.py'}}]}))
    cortex = AttemptCortex(store, p['host'], first.id, first.fence)
    try:
        actions = cortex.generate_candidates(context())
        assert len(actions) == 1 and actions[0].tool == 'read_file'
        tools = model_server.requests[-1]['response_format']['json_schema']['schema']['properties']['candidates']['items']['properties']['tool']['enum']
        assert 'send_message' not in tools
    finally:
        cortex.core.reasoner._client.close()


def test_revoke_of_unadvertised_future_route_does_not_discard_plain_http_work(message_siblings, model_server):
    from flycode.orchestration.cortex import AttemptCortex
    from flycode.orchestration.store import Store

    p, store = message_siblings, message_siblings['store']
    first = claim_sibling(p, 'child', 0)
    revoke = request(p, key='revoke-unadvertised', head=p['message_grant'], edges=[])
    entered = []

    def respond(payload):
        tools = payload['response_format']['json_schema']['schema']['properties']['candidates']['items']['properties']['tool']['enum']
        assert 'send_message' not in tools
        connection = Store(store.path, host=store.host)
        try:
            assert OwnerCommands(connection).execute(revoke).error_code is None
            entered.append(True)
        finally:
            connection.close()
        return model_server.completion(json.dumps({'candidates': [{'tool': 'read_file',
            'arguments': {'path': 'src/parser.py'}}]}))

    model_server.respond = respond
    cortex = AttemptCortex(store, p['host'], first.id, first.fence)
    try:
        actions = cortex.generate_candidates(context())
        assert entered and len(actions) == 1 and actions[0].tool == 'read_file', cortex.last_routing
        assert not cortex.last_routing.error
    finally:
        cortex.core.reasoner._client.close()


def test_lost_host_after_tool_admission_cannot_publish_message(message_siblings, model_server, monkeypatch):
    from test_host_leadership import replace_host_lock

    from flycode.orchestration.store import StoreError
    from flycode.orchestration.tools import Tools

    p, store = message_siblings, message_siblings['store']
    first, second = claim_sibling(p, 'child', 0), claim_sibling(p, 'second', 1)
    cortex, action = selected_question(p, model_server, first, second)
    before = store.budget(p['owner'], p['workflow'].id).spent
    begin = Tools.begin

    def lose_host(service, *args, **kwargs):
        result = begin(service, *args, **kwargs)
        replace_host_lock(store.host)
        return result

    monkeypatch.setattr(Tools, 'begin', lose_host)
    try:
        with pytest.raises(StoreError, match='fence'):
            cortex.execute_message(p['source'], action)
        assert Messages(store).journal(p['owner'], p['workflow'].id) == []
        assert store.db.execute('SELECT count(*) FROM message_route_proofs').fetchone()[0] == 0
        assert store.budget(p['owner'], p['workflow'].id).spent.tool_calls == before.tool_calls + 1
        call, = store.db.execute('SELECT data FROM tool_calls WHERE attempt_id=?', (first.id,)).fetchall()
        assert json.loads(call['data'])['late']
    finally:
        cortex.core.reasoner._client.close()


def test_actual_worker_progress_uses_dynamic_owner_routes(message_siblings, model_server):
    p, store = message_siblings, message_siblings['store']
    second = claim_sibling(p, 'second', 1)
    model_server.respond = lambda request: model_server.completion(json.dumps({'candidates': [{
        'tool': 'read_file', 'arguments': {'path': 'src/parser.py'}}]}))
    result = p['dispatcher'].tick(p['host'], key='start-dynamic-progress-worker')
    assert result.attempt is not None and result.attempt.task_id == p['child'].id
    wait_handles(p['dispatcher'])
    progress = [message for message in Messages(store).journal(p['owner'], p['workflow'].id)
        if message.content.kind == 'progress']
    assert progress, 'Actual controller activity never reached the granted peer'
    assert all(message.attempt_id == result.attempt.id and message.recipient_attempt_id == second.id for message in progress)
    assert all(store.db.execute('SELECT grant_id FROM message_route_proofs WHERE message_id=?',
        (message.id,)).fetchone()[0] == p['message_grant'] for message in progress)
    assert not [json.loads(row['data']) for row in store.db.execute('SELECT data FROM tool_calls WHERE attempt_id=?',
        (result.attempt.id,)) if json.loads(row['data'])['tool'] == 'send_message']
