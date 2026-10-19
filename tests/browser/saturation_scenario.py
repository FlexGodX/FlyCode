"""Owner-approved 64-task hierarchy for bounded native pressure measurements."""


from flycode.domain.orchestration import (
    AgentProfileDraft,
    BudgetAmount,
    TaskDraft,
    WorkflowBudget,
    WorkflowDraft,
)
from flycode.domain.verification_control import VerificationProfileDraft
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.store import changed
from flycode.verification.engine import VerificationEngine
from flycode.verification.registry import VerificationRegistry

NAMES = ('parser', 'second', 'charlie', 'delta', 'echo', *(f'output_{n:02d}' for n in range(6, 28)))


def prepare(managed, store, server, root):
    observer, owner, runtime, old_workflow, old_tasks, source, _, runtime_root, _, checks, _ = managed
    developer = observer._profile(old_tasks[1].allowed_profile_ids[0])
    permission = changed(developer.permissions, tools=(*developer.permissions.tools, 'send_message'))
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **developer.model_dump(include=set(AgentProfileDraft.model_fields)), 'permissions': permission,
        'context_limit': 16384, 'instructions': 'Repair only the assigned output and use approved peer routes.'}))
    assert profile.context_limit == profile.context_capacity == 16384
    instances = [store.register_instance(runtime, profile.id) for _ in range(5)]
    reviewer = observer._profile(old_tasks[2].allowed_profile_ids[0])
    root_contract = old_tasks[0].acceptance.model_copy(update={'id': 'pressure64-root'})
    workflow = store.create_workflow(owner, WorkflowDraft(project_id=old_workflow.project_id,
        task=TaskDraft(title='Prepare and finish the checked hierarchy',
            objective='Prepare note phase 1, wait for verified children, then set extra value to 4.',
            acceptance=root_contract, permissions=permission, budget=BudgetAmount(model_calls=2, tool_calls=2),
            allowed_profile_ids=(profile.id,), capabilities=('python',)), budget=WorkflowBudget()), key='pressure64-workflow')
    parent = store.task(owner, workflow.root_task_id)
    groups = [store.create_task(owner, workflow.id, TaskDraft(title=f'Checked group {n}',
        objective='Verify every required output in this group against the owner criteria.',
        parent_id=parent.id, acceptance=root_contract, permissions=permission, budget=BudgetAmount()),
        key=f'pressure64-group-{n}') for n in range(7)]
    (source / 'src/note.py').write_text('phase = 0\n')
    (source / 'src/extra.py').write_text('value = 0\n')
    gates = root / 'gates'
    gates.mkdir()
    gate_source = 'from pathlib import Path\nimport runpy, time\n'
    for name in NAMES:
        (source / f'src/{name}.py').write_text('value = 0\n')
    for name in NAMES[:5]:
        gate_source += (f'\ndef test_{name}():\n'
            f'    gate = Path({str(gates)!r})\n'
            f'    (gate / "{name}-started").write_text("started")\n'
            '    deadline = time.monotonic() + 60\n'
            f'    while not (gate / "{name}-release").exists():\n'
            '        assert time.monotonic() < deadline, "Owned pressure gate deadline"\n'
            '        time.sleep(.02)\n'
            f'    assert runpy.run_path("src/{name}.py")["value"] == 1\n')
    (source / 'tests/test_saturation_gate.py').write_text(gate_source)
    writers, reviewers, children = [parent], [], {}
    for number, name in enumerate(NAMES):
        contract = root_contract.model_copy(update={'id': 'pressure64-' + name})
        writer = store.create_task(owner, workflow.id, TaskDraft(title='Repair ' + name,
            objective=f'Hold pressure slot {name}, then set its approved output to 1.',
            parent_id=groups[(2 * number) // 8].id, acceptance=contract,
            permissions=changed(permission, write_scopes=(f'src/{name}.py',)),
            budget=BudgetAmount(model_calls=2, tool_calls=2), allowed_profile_ids=(profile.id,),
            capabilities=('python',), dependencies=() if number < 5 else (writers[1].id,)),
            key='pressure64-writer-' + name)
        writers.append(writer)
        children[name] = writer
        review = store.create_task(owner, workflow.id, TaskDraft(title='Review ' + name, kind='review',
            objective='Independently verify the exact assigned output.',
            parent_id=groups[(2 * number + 1) // 8].id, acceptance=contract, permissions=reviewer.permissions,
            budget=BudgetAmount(tool_calls=4), allowed_profile_ids=(reviewer.id,)), key='pressure64-review-' + name)
        reviewers.append(review)
    parent_review = store.create_task(owner, workflow.id, TaskDraft(title='Review parent continuation', kind='review',
        objective='Verify the complete owner hierarchy independently.', parent_id=groups[6].id,
        acceptance=root_contract, permissions=reviewer.permissions, budget=BudgetAmount(tool_calls=4),
        allowed_profile_ids=(reviewer.id,)), key='pressure64-parent-review')
    reviewers.insert(0, parent_review)
    integrator = store.create_task(owner, workflow.id, TaskDraft(title='Integrate the checked hierarchy', kind='review',
        objective='Apply only reviewed exact output and verify all owner criteria.', parent_id=parent.id,
        dependencies=tuple(task.id for task in reviewers), acceptance=root_contract,
        permissions=reviewer.permissions, budget=BudgetAmount(tool_calls=120), allowed_profile_ids=(reviewer.id,)),
        key='pressure64-integrator')
    tasks = store.tasks(owner, workflow.id)
    assert len(tasks) == 64 and len(writers) == len(reviewers) == 28
    assert max(sum(task.parent_id == ancestor.id for task in tasks) for ancestor in tasks) == 8
    engine = VerificationEngine(runtime_root / 'verification')
    registry = VerificationRegistry(store)
    approvals, pins, registered = {}, {}, {}
    by_writer = {task.id: (task.acceptance, f'    assert runpy.run_path("src/{name}.py")["value"] == 1\n')
        for name, task in children.items()}
    combined = ('    assert runpy.run_path("src/note.py")["phase"] == 1\n'
        '    assert runpy.run_path("src/extra.py")["value"] == 4\n'
        + ''.join(f'    assert runpy.run_path("src/{name}.py")["value"] == 1\n' for name in NAMES))
    by_writer[parent.id] = (root_contract, combined)
    for writer, review in zip(writers, reviewers):
        contract, body = by_writer[writer.id]
        oracle = root / ('oracle-' + contract.id)
        (oracle / 'tests').mkdir(parents=True)
        (oracle / 'tests/test_parser.py').write_text('import runpy\n\ndef test_parse():\n' + body)
        approval = engine.oracles.approve(contract, checks, oracle)
        saved = engine.oracles.read(approval)
        pin = registry.register_profile(owner, workflow.project_id,
            VerificationProfileDraft(title='Owner exact hierarchy criterion', acceptance=contract, checks=saved.checks),
            oracle_root=runtime_root / 'verification/oracles' / approval / 'files')
        registered[contract.id] = approval, pin
        for task in (writer, review):
            approvals[task.id], pins[task.id] = approval, pin
    for task in (*groups, integrator):
        approvals[task.id], pins[task.id] = registered[root_contract.id]
    dispatcher = Dispatcher(store, runtime_root)
    for writer in writers:
        current = store.task(owner, writer.id)
        dispatcher.bind(owner, writer.id, source, expected_revision=current.revision,
            key='pressure64-bind-' + writer.id, brain='heuristic_fsm')
    recipe = Pipelines(store, runtime_root).approve_parent(owner, workflow.id,
        {writer.id: review.id for writer, review in zip(writers, reviewers)}, integrator.id,
        source=source, expected_revision=store.workflow(owner, workflow.id).revision,
        expected_revisions={task.id: task.revision for task in store.tasks(owner, workflow.id)},
        approval_hashes=approvals, profile_pins=pins, preparation_steps=1, key='pressure64-approve')
    calls = {}

    def respond(request):
        prompt = request['messages'][-1]['content']
        if parent.objective in prompt:
            arguments = {'path': 'src/note.py', 'old_text': 'phase = 0', 'new_text': 'phase = 1'}
            action = {'tool': 'edit_file', 'arguments': arguments}
        else:
            name, = [name for name, task in children.items() if task.objective in prompt]
            calls[name] = calls.get(name, 0) + 1
            if name in NAMES[:5] and calls[name] == 1:
                action = {'tool': 'run_tests', 'arguments': {'target': f'tests/test_saturation_gate.py::test_{name}', 'timeout': 60}}
            else:
                action = {'tool': 'edit_file', 'arguments': {'path': f'src/{name}.py',
                    'old_text': 'value = 0', 'new_text': 'value = -1' if name == 'parser' else 'value = 1'}}
        import json
        return server.completion(json.dumps({'candidates': [action]}))

    server.respond = respond
    return {'store': store, 'owner': owner, 'runtime': runtime, 'workflow': workflow, 'parent': parent,
        'groups': groups, 'writers': writers, 'reviewers': reviewers, 'integrator': integrator,
        'instances': instances, 'recipe': recipe, 'dispatcher': dispatcher, 'source': source,
        'gates': gates, 'children': children, 'profile_pins': pins, 'approvals': approvals}
