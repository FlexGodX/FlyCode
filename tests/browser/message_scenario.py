"""English model requests and private pytest gates for routed sibling delivery."""

import json
import re
import time

from flycode.domain.orchestration import (
    AgentProfileDraft,
    BudgetAmount,
    TaskCommand,
    TaskDraft,
)
from flycode.orchestration.store import changed
from flycode.verification.engine import VerificationEngine


def configure(p, server, root):
    store, owner = p['store'], p['owner']
    gates = root / 'gates'
    gates.mkdir()
    for name in ('a1', 'a2', 'boot', 'b'):
        (p['source'] / f'tests/test_route_{name}.py').write_text(
            'from pathlib import Path\nimport runpy, time\n\ndef test_gate():\n'
            f'    Path({str(gates / (name + "-started"))!r}).write_text("started")\n'
            '    deadline = time.monotonic() + 45\n'
            f'    while not Path({str(gates / (name + "-release"))!r}).exists():\n'
            '        assert time.monotonic() < deadline, "Peer gate deadline"\n'
            '        time.sleep(.05)\n'
            + ('    assert runpy.run_path("src/parser.py")["value"] == 1\n' if name in {'a1', 'a2'}
                else '    assert runpy.run_path("src/second.py")["value"] == 2\n'))
    (p['source'] / 'src/second.py').write_text('value = 0\n')
    old = store._profile(p['child'].allowed_profile_ids[0])
    permission = changed(old.permissions, tools=(*old.permissions.tools, 'send_message'))
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **old.model_dump(include=set(AgentProfileDraft.model_fields)), 'permissions': permission}))
    for _ in range(2):
        store.register_instance(p['host'], profile.id)

    def revise(name, **changes):
        task = store.task(owner, p[name].id)
        p[name] = store.command(owner, task.id, TaskCommand(operation='revise', expected_revision=task.revision,
            idempotency_key='message-automatic-' + name, changes=changes))

    parent_contract = p['parent'].acceptance.model_copy(update={'id': 'message-parent-acceptance'})
    revise('parent', acceptance=parent_contract.model_dump(mode='json'),
        allowed_profile_ids=(profile.id,), permissions=permission.model_dump(mode='json'))
    revise('child', objective='Repair parser value to 1 and answer the sibling question through the approved route.',
        allowed_profile_ids=(profile.id,), permissions=changed(permission, write_scopes=('src/parser.py',)).model_dump(mode='json'),
        budget=BudgetAmount(model_calls=4, tool_calls=4).model_dump())
    revise('parent_review', acceptance=parent_contract.model_dump(mode='json'))
    second_contract = p['child'].acceptance.model_copy(update={'id': 'message-second-acceptance'})
    second = store.create_task(owner, p['workflow'].id, TaskDraft.model_validate({
        **p['child'].model_dump(include=set(TaskDraft.model_fields)), 'title': 'Repair second sibling',
        'objective': 'Ask the sibling for its parser result and set second value to that result plus 1.',
        'acceptance': second_contract, 'permissions': changed(permission, write_scopes=('src/second.py',))}),
        key='automatic-second-sibling')
    review = store.create_task(owner, p['workflow'].id, TaskDraft.model_validate({
        **p['child_review'].model_dump(include=set(TaskDraft.model_fields)), 'title': 'Review second sibling',
        'acceptance': second_contract}), key='automatic-second-review')
    p.update(second=second, second_review=review)
    revise('integrator', acceptance=parent_contract.model_dump(mode='json'),
        dependencies=(p['child_review'].id, p['parent_review'].id, review.id),
        budget=BudgetAmount(tool_calls=35).model_dump())
    integrator = store.task(owner, p['integrator'].id)
    if integrator.state == 'blocked':
        p['integrator'] = store.command(owner, integrator.id, TaskCommand(operation='reopen',
            expected_revision=integrator.revision, idempotency_key='automatic-message-integrator-reopen'))
    engine = VerificationEngine(p['dispatcher'].root / 'verification')
    checks = engine.oracles.read(p['approvals'][p['child'].id]).checks
    for name, contract, body, task_ids in (
        ('second', second_contract, '    assert runpy.run_path("src/second.py")["value"] == 2\n', (second.id, review.id)),
        ('combined', parent_contract, ('    assert runpy.run_path("src/parser.py")["value"] == 1\n'
            '    assert runpy.run_path("src/note.py")["phase"] == 1\n'
            '    assert runpy.run_path("src/extra.py")["value"] == 4\n'
            '    assert runpy.run_path("src/second.py")["value"] == 2\n'),
            (p['parent'].id, p['parent_review'].id, p['integrator'].id)),
    ):
        oracle = root / ('message-oracle-' + name)
        (oracle / 'tests').mkdir(parents=True)
        (oracle / 'tests/test_parser.py').write_text('import runpy\n\ndef test_parse():\n' + body)
        approval = engine.oracles.approve(contract, checks, oracle)
        p['approvals'].update({identity: approval for identity in task_ids})
    original, calls, observed = server.respond, {'a': 0, 'b': 0}, []

    def entries(prompt):
        result = []
        for line in prompt.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and {'message_id', 'sender_id', 'content'} <= set(value):
                result.append(value)
        return result

    def gate(name):
        return {'tool': 'run_tests', 'arguments': {'target': f'tests/test_route_{name}.py::test_gate', 'timeout': 45}}

    def respond(request):
        prompt = request['messages'][-1]['content']
        if p['parent'].objective in prompt:
            return original(request)
        if p['child'].objective in prompt:
            calls['a'] += 1
            number = calls['a']
            if number == 1:
                action = gate('a1')
            elif number == 2:
                question, = [entry for entry in entries(prompt) if entry['content']['kind'] == 'question']
                action = {'tool': 'send_message', 'arguments': {'recipients': [question['sender_id']],
                    'causation_id': question['message_id'], 'content': {'kind': 'answer', 'answer': 'Use parser result 1.'}}}
                observed.append({'kind': 'question_consumed', 'message_id': question['message_id']})
            elif number == 3:
                action = gate('a2')
            else:
                assert number == 4
                action = {'tool': 'edit_file', 'arguments': {'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}
        else:
            assert p['second'].objective in prompt
            calls['b'] += 1
            number = calls['b']
            if number == 1:
                action = gate('boot')
            elif number == 2:
                match = re.search(r'Host-approved send_message recipients: (\[[^\n]+\])', prompt)
                assert match
                recipients = json.loads(match.group(1))
                assert len(recipients) == 1
                action = {'tool': 'send_message', 'arguments': {'recipients': recipients,
                    'content': {'kind': 'question', 'question': 'Which parser result should the second value use?'}}}
            elif number == 3:
                action = gate('b')
            else:
                assert number == 4
                answer, = [entry for entry in entries(prompt) if entry['content']['kind'] == 'answer']
                match = re.fullmatch(r'Use parser result ([0-9]+)\.', answer['content']['answer'])
                assert match
                value = int(match.group(1)) + 1
                action = {'tool': 'edit_file', 'arguments': {'path': 'src/second.py', 'old_text': 'value = 0', 'new_text': f'value = {value}'}}
                observed.append({'kind': 'answer_consumed', 'message_id': answer['message_id'], 'derived_value': value})
                (gates / 'answer-consumed').write_text(answer['message_id'])
        observed.append({'kind': 'model_action', 'at': time.time(), 'action': action})
        return server.completion(json.dumps({'candidates': [action]}))

    server.respond = respond
    return {'gates': gates, 'calls': calls, 'observed': observed}
