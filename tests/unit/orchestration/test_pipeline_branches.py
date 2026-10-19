"""New owner-approved branches extend a graph without replacing old task IDs."""

import json

import pytest
from test_parent_continuation import (  # noqa: F401
    approve_parent,
    parent_ready,
    review_ready,
    wait_handles,
)
from test_pipeline_dispatch import pipeline_ready  # noqa: F401
from test_pipeline_retries import retry_ready  # noqa: F401
from test_store import setup  # noqa: F401

from flycode.domain.orchestration import BudgetAmount, TaskCommand, TaskDraft
from flycode.orchestration.integration import Integration
from flycode.orchestration.pipeline import Pipelines
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def branch_ready(parent_ready, model_server, request):  # noqa: F811
    p = parent_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    config = getattr(request, 'param', {})
    suspend_only = config.get('suspend_only', False)
    if config.get('workflow_budget'):
        from flycode.orchestration.store import changed

        workflow = store.workflow(owner, p['workflow'].id)
        store._save_workflow(changed(workflow, budget=changed(workflow.budget, **config['workflow_budget'])))
    integrator = p['integrator']
    p['integrator'] = store.command(owner, integrator.id, TaskCommand(operation='revise', expected_revision=integrator.revision,
        idempotency_key='initial-branch-integration-budget', changes={'budget': BudgetAmount(tool_calls=config.get('integration_tools', 35)).model_dump()}))
    (p['source'] / 'src/second.py').write_text('value = 0\n')
    for task in (p['parent'], p['child']):
        previous = dispatcher.binding(owner, task.id)
        dispatcher.bind(owner, task.id, p['source'], expected_revision=task.revision,
            expected_binding_id=previous.id, key='branch-source-' + task.id, brain='heuristic_fsm')
    recipe = approve_parent(p)
    for number in range(12):
        dispatcher.tick(host, key=f'before-branch-{number}')
        wait_handles(dispatcher)
        if suspend_only or store.task(owner, p['child_review'].id).state == 'succeeded':
            break
    service = Pipelines(store, dispatcher.root)
    assert service.advance(host, recipe.workflow_id) == ()
    assert store.task(owner, p['child'].id).state == ('ready' if suspend_only else 'succeeded')
    assert store.task(owner, p['parent'].id).state == 'blocked'
    contract = p['child'].acceptance.model_copy(update={'id': 'added-child-acceptance'})
    oracle = p['source'].parent / 'added-child-oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_parser.py').write_text('import runpy\ndef test_parse():\n'
        + ('' if suspend_only else '    assert runpy.run_path("src/parser.py")["value"] == 1\n')
        + '    assert runpy.run_path("src/second.py")["value"] == 2\n')
    engine = VerificationEngine(dispatcher.root / 'verification')
    approval = engine.oracles.approve(contract, engine.oracles.read(p['approvals'][p['child'].id]).checks, oracle.parent)
    writer = TaskDraft.model_validate({**p['child'].model_dump(include=set(TaskDraft.model_fields)),
        'title': 'Added child', 'objective': 'Set added second value to 2 after the confirmed parser repair.',
        'acceptance': contract, 'dependencies': (p['child'].id,), 'budget': BudgetAmount(model_calls=1, tool_calls=1)})
    reviewer = TaskDraft.model_validate({**p['child_review'].model_dump(include=set(TaskDraft.model_fields)),
        'title': 'Review added child', 'acceptance': contract})
    p['branch_addition'] = {'writer': writer.model_dump(mode='json'), 'reviewer': reviewer.model_dump(mode='json'),
        'template_writer_id': p['child'].id, 'approval_hash': approval}
    original = model_server.respond
    def respond(request):
        if writer.objective not in str(request['messages']):
            return original(request)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/second.py', 'old_text': 'value = 0', 'new_text': 'value = 2'}}]}))
    model_server.respond = respond
    try:
        yield p, recipe
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def test_owner_adds_new_branch_with_server_ids_and_retains_original_completion(branch_ready):
    p, recipe = branch_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    service = Pipelines(store, dispatcher.root)
    previous = store.task(owner, p['child'].id)
    proof = previous.completion_id
    before = store.budget(owner, recipe.workflow_id)
    deadline = store.workflow(owner, recipe.workflow_id).deadline_at
    arguments = {'changes': {}, 'new_branches': [p['branch_addition']], 'key': 'add-branch',
        'expected_revision': store.workflow(owner, recipe.workflow_id).revision,
        'expected_revisions': {task.id: task.revision for task in store.tasks(owner, recipe.workflow_id)}}
    revised = service.replan(owner, recipe.id, **arguments)
    assert revised.graph_revision == 1 and len(revised.branches) == 3
    assert service.replan(owner, recipe.id, **arguments) == revised
    added = set(revised.tasks) - set(recipe.tasks)
    assert len(added) == 2 and set(recipe.tasks) <= set(revised.tasks)
    assert store.task(owner, previous.id) == previous and previous.completion_id == proof
    assert store.budget(owner, recipe.workflow_id) == before
    assert store.workflow(owner, recipe.workflow_id).semantic_revision == recipe.workflow_semantic_revision
    for number in range(25):
        dispatcher.tick(host, key=f'after-branch-{number}')
        wait_handles(dispatcher)
        if store.workflow(owner, recipe.workflow_id).state == 'succeeded':
            break
    assert store.workflow(owner, recipe.workflow_id).state == 'succeeded'
    assert store.workflow(owner, recipe.workflow_id).deadline_at == deadline
    assert store.task(owner, previous.id).completion_id == proof
    assert (p['source'] / 'src/second.py').read_text() == 'value = 2\n'
    assert Integration(store, dispatcher.root).current(owner, store.task(owner, p['parent'].id).result_id)
    attempts = store.attempts(owner, recipe.workflow_id)
    assert len(attempts) == 8
    ledger = store.budget(owner, recipe.workflow_id)
    assert ledger.spent.model_calls == 4 and ledger.reserved == BudgetAmount() and not ledger.uncertain


def addition_arguments(p, recipe, **values):
    store, owner = p['store'], p['owner']
    return {'changes': {}, 'new_branches': [p['branch_addition']], 'key': 'add-branch',
        'expected_revision': store.workflow(owner, recipe.workflow_id).revision,
        'expected_revisions': {task.id: task.revision for task in store.tasks(owner, recipe.workflow_id)}, **values}


def unchanged(p, recipe):
    store, owner = p['store'], p['owner']
    return (store.tasks(owner, recipe.workflow_id), store.attempts(owner, recipe.workflow_id), store.budget(owner, recipe.workflow_id),
        store.outbox_count(), store._counter('task_number'), tuple(map(tuple, store.db.execute('SELECT * FROM pipeline_receipts ORDER BY stage'))))


@pytest.mark.parametrize('mutation', ['objective', 'acceptance', 'permission', 'writer_budget', 'review_budget', 'review_kind',
    'review_tools', 'writer_role', 'profile', 'parent', 'edge', 'alias', 'template', 'oracle', 'criteria_pair', 'canonical_id', 'model_instance'])
def test_invalid_addition_is_inert(branch_ready, mutation):
    from flycode.domain.pipeline import BranchAddition

    p, recipe = branch_ready
    addition = json.loads(json.dumps(p['branch_addition']))
    if mutation in {'objective', 'acceptance', 'permission', 'writer_budget'}:
        field, value = {'objective': ('objective', ' '), 'acceptance': ('acceptance', None),
            'permission': ('permissions', None), 'writer_budget': ('budget', None)}[mutation]
        addition['writer'][field] = value
    elif mutation == 'review_budget':
        addition['reviewer']['budget'] = {'tool_calls': 0}
    elif mutation == 'review_kind':
        addition['reviewer']['kind'] = 'implementation'
    elif mutation == 'review_tools':
        addition['reviewer']['permissions']['tools'] = ['read_file']
    elif mutation == 'writer_role':
        addition['writer']['allowed_profile_ids'] = addition['reviewer']['allowed_profile_ids']
    elif mutation == 'profile':
        addition['writer']['allowed_profile_ids'] = ['foreign-profile']
    elif mutation == 'parent':
        addition['writer']['parent_id'] = p['child'].id
    elif mutation in {'edge', 'alias'}:
        addition['writer']['dependencies'] = ['foreign-task' if mutation == 'edge' else 'new:9']
    elif mutation == 'template':
        addition['template_writer_id'] = 'foreign-template'
    elif mutation == 'oracle':
        addition['approval_hash'] = '0' * 64
    elif mutation == 'criteria_pair':
        addition['reviewer']['acceptance']['version'] += 1
    elif mutation == 'canonical_id':
        addition['writer']['id'] = p['child'].id
    else:
        addition = BranchAddition.model_validate(addition)
        addition = addition.model_copy(update={'writer': addition.writer.model_copy(update={'objective': True})})
    before = unchanged(p, recipe)
    with pytest.raises(ValueError):
        Pipelines(p['store'], p['dispatcher'].root).replan(p['owner'], recipe.id,
            **addition_arguments(p, recipe, new_branches=[addition]))
    assert unchanged(p, recipe) == before


def test_addition_counter_bindings_integrator_and_outbox_rollback_together(branch_ready, monkeypatch):
    from flycode.orchestration.store import Store

    p, recipe = branch_ready
    store, owner = p['store'], p['owner']
    before = unchanged(p, recipe)
    original = store._event
    def fail(scope, workflow, kind, **values):
        if kind == 'pipeline_replanned':
            raise RuntimeError('outbox unavailable')
        return original(scope, workflow, kind, **values)
    monkeypatch.setattr(store, '_event', fail)
    with pytest.raises(RuntimeError, match='outbox unavailable'):
        Pipelines(store, p['dispatcher'].root).replan(owner, recipe.id, **addition_arguments(p, recipe))
    assert unchanged(p, recipe) == before
    monkeypatch.setattr(store, '_event', original)
    arguments = addition_arguments(p, recipe)
    revised = Pipelines(store, p['dispatcher'].root).replan(owner, recipe.id, **arguments)
    after = unchanged(p, recipe)
    connection = Store(store.path, catalogue=store.catalogue)
    try:
        assert Pipelines(connection, p['dispatcher'].root).replan(owner, recipe.id, **arguments) == revised
        assert unchanged(p, recipe) == after
    finally:
        connection.close()


@pytest.mark.parametrize('branch_ready', [{'suspend_only': True}], indirect=True)
def test_existing_child_waits_for_new_helper_in_same_atomic_graph_revision(branch_ready, model_server):
    from flycode.orchestration.artifacts import Artifacts

    p, recipe = branch_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    addition = json.loads(json.dumps(p['branch_addition']))
    addition['writer']['dependencies'] = []
    objective = 'Repair parser value to 1 after the new helper has set second value to 2.'
    original = model_server.respond
    def respond(request):
        if objective not in str(request['messages']):
            return original(request)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
            'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
    model_server.respond = respond
    before = store.budget(owner, recipe.workflow_id)
    revised = Pipelines(store, dispatcher.root).replan(owner, recipe.id, **addition_arguments(p, recipe,
        changes={p['child'].id: {'objective': objective, 'dependencies': ['new:0']}}, new_branches=[addition]))
    new = next(branch for branch in revised.branches if branch.writer_id not in recipe.tasks)
    assert store.task(owner, p['child'].id).dependencies == (new.writer_id,)
    assert store.budget(owner, recipe.workflow_id) == before
    assert all(not dep.startswith('new:') for task in store.tasks(owner, recipe.workflow_id) for dep in task.dependencies)
    for number in range(30):
        dispatcher.tick(host, key=f'helper-dependency-{number}')
        wait_handles(dispatcher)
        if store.workflow(owner, recipe.workflow_id).state == 'succeeded':
            break
    assert store.workflow(owner, recipe.workflow_id).state == 'succeeded'
    child = store.task(owner, p['child'].id)
    author = next(attempt for attempt in store.attempts(owner, recipe.workflow_id) if attempt.task_id == child.id)
    assert author.prerequisites[new.writer_id].id == store.task(owner, new.writer_id).completion_id
    artifact = Artifacts(store, dispatcher.root).for_attempt(owner, author.id)
    assert (dispatcher.root / 'artifacts' / artifact.id / 'baseline/src/second.py').read_text() == 'value = 2\n'
    assert (p['source'] / 'src/parser.py').read_text() == 'value = 1\n'
    assert Integration(store, dispatcher.root).current(owner, store.task(owner, p['parent'].id).result_id)


@pytest.mark.parametrize('branch_ready', [{'suspend_only': True}], indirect=True)
@pytest.mark.parametrize('invalid', [False, 0, ''])
def test_false_scalar_additions_cannot_masquerade_as_missing(branch_ready, invalid):
    from pydantic import ValidationError

    p, recipe = branch_ready
    before = unchanged(p, recipe)
    with pytest.raises(ValidationError):
        Pipelines(p['store'], p['dispatcher'].root).replan(p['owner'], recipe.id,
            **addition_arguments(p, recipe, changes={p['child'].id: {'objective': 'A valid owner revision.'}}, new_branches=invalid))
    assert unchanged(p, recipe) == before


@pytest.mark.parametrize('branch_ready', [{'suspend_only': True}], indirect=True)
@pytest.mark.parametrize('cycle', ['self', 'existing'])
def test_new_alias_cycles_roll_back_server_ids_and_old_tasks(branch_ready, cycle):
    from flycode.orchestration.store import StoreError

    p, recipe = branch_ready
    addition = json.loads(json.dumps(p['branch_addition']))
    addition['writer']['dependencies'] = ['new:0' if cycle == 'self' else p['child'].id]
    changes = {} if cycle == 'self' else {p['child'].id: {'dependencies': ['new:0']}}
    before = unchanged(p, recipe)
    with pytest.raises(StoreError, match='cycle'):
        Pipelines(p['store'], p['dispatcher'].root).replan(p['owner'], recipe.id,
            **addition_arguments(p, recipe, new_branches=[addition], changes=changes))
    assert unchanged(p, recipe) == before


def test_new_branch_model_checks_never_hold_sql_writer_lock(branch_ready, monkeypatch):
    p, recipe = branch_ready
    store, owner = p['store'], p['owner']
    original = store._binding_current
    def available(profile):
        assert not store.db.in_transaction
        return original(profile)
    monkeypatch.setattr(store, '_binding_current', available)
    revised = Pipelines(store, p['dispatcher'].root).replan(owner, recipe.id, **addition_arguments(p, recipe))
    assert revised.graph_revision == 1


@pytest.mark.parametrize('mutation', ['profile', 'template', 'pause', 'source'])
def test_addition_rechecks_owner_authority_after_preflight(branch_ready, monkeypatch, mutation):
    from flycode.orchestration.branching import Branching
    from flycode.orchestration.store import StoreError, changed

    p, recipe = branch_ready
    store, owner = p['store'], p['owner']
    original = Branching.prepare
    def revoked(service, scope, current, tasks, attempts, additions, oracles):
        result = original(service, scope, current, tasks, attempts, additions, oracles)
        if mutation == 'profile':
            profile = next(iter(result[1].values()))
            profile = changed(profile, instructions='New owner instructions', version=profile.version + 1)
            store.db.execute('UPDATE profiles SET data=? WHERE id=?', (profile.model_dump_json(), profile.id))
        elif mutation == 'template':
            template = next(iter(result[2].values()))
            template = changed(template, seed=template.seed + 1)
            store.db.execute('UPDATE worker_bindings SET data=? WHERE id=?', (template.model_dump_json(), template.id))
        elif mutation == 'pause':
            task = store.task(owner, p['parent'].id)
            store.command(owner, task.id, TaskCommand(operation='pause', expected_revision=task.revision, idempotency_key='pause-during-addition'))
        else:
            (p['source'] / 'dirty.bin').write_bytes(b'new owner bytes')
        return result
    monkeypatch.setattr(Branching, 'prepare', revoked)
    before_counter, before_ids = store._counter('task_number'), {task.id for task in store.tasks(owner, recipe.workflow_id)}
    with pytest.raises(StoreError):
        Pipelines(store, p['dispatcher'].root).replan(owner, recipe.id, **addition_arguments(p, recipe))
    assert store._counter('task_number') == before_counter
    assert {task.id for task in store.tasks(owner, recipe.workflow_id)} == before_ids
    assert not store.db.execute("SELECT 1 FROM pipeline_receipts WHERE pipeline_id=? AND stage LIKE 'graph:%'", (recipe.id,)).fetchone()


def test_simultaneous_additions_allocate_one_pair_and_no_lost_short_ids(branch_ready, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from flycode.orchestration.branching import Branching
    from flycode.orchestration.store import Store, StoreError

    p, recipe = branch_ready
    store, owner = p['store'], p['owner']
    arguments = addition_arguments(p, recipe)
    barrier, original = Barrier(2), Branching.prepare
    def prepared(*values):
        result = original(*values)
        barrier.wait(10)
        return result
    monkeypatch.setattr(Branching, 'prepare', prepared)
    def add(number):
        connection = Store(store.path, catalogue=store.catalogue)
        try:
            return Pipelines(connection, p['dispatcher'].root).replan(owner, recipe.id,
                **{**arguments, 'key': f'concurrent-addition-{number}'})
        except StoreError as error:
            return error.code
        finally:
            connection.close()
    before = store._counter('task_number')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(add, [1, 2]))
    assert sum(hasattr(result, 'graph_revision') for result in results) == 1
    assert [result for result in results if isinstance(result, str)] == ['revision']
    assert store._counter('task_number') == before + 2
    tasks = store.tasks(owner, recipe.workflow_id)
    assert len(tasks) == len(recipe.tasks) + 2 and len({task.short_id for task in tasks}) == len(tasks)


@pytest.mark.parametrize('branch_ready', [{'workflow_budget': {'max_tasks': 6}}, {'workflow_budget': {'max_children': 5}}], indirect=True)
def test_addition_counts_existing_tasks_and_children_before_committing(branch_ready):
    from flycode.orchestration.store import StoreError

    p, recipe = branch_ready
    before = unchanged(p, recipe)
    with pytest.raises(StoreError, match='task_limit|children'):
        Pipelines(p['store'], p['dispatcher'].root).replan(p['owner'], recipe.id, **addition_arguments(p, recipe))
    assert unchanged(p, recipe) == before


@pytest.mark.parametrize('branch_ready', [{'suspend_only': True}], indirect=True)
def test_oversized_addition_request_is_rejected_before_graph_preflight(branch_ready, monkeypatch):
    from pydantic import ValidationError

    from flycode.orchestration.branching import Branching

    p, recipe = branch_ready
    monkeypatch.setattr(Branching, 'prepare', lambda *a: pytest.fail('Oversized request reached graph admission'))
    before = unchanged(p, recipe)
    with pytest.raises(ValidationError):
        Pipelines(p['store'], p['dispatcher'].root).replan(p['owner'], recipe.id,
            **addition_arguments(p, recipe, new_branches=[p['branch_addition']] * 31))
    assert unchanged(p, recipe) == before


@pytest.mark.parametrize('branch_ready', [{'integration_tools': 45}], indirect=True)
def test_new_branches_can_depend_on_each_other_before_server_ids_exist(branch_ready, model_server):
    from flycode.domain.orchestration import AgentProfileDraft
    from flycode.orchestration.store import changed

    p, recipe = branch_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    first = json.loads(json.dumps(p['branch_addition']))
    old_profile = store._profile(p['child'].allowed_profile_ids[0])
    permission = changed(old_profile.permissions, tools=(*old_profile.permissions.tools, 'create_file'))
    profile = store.register_profile(owner, AgentProfileDraft.model_validate({
        **old_profile.model_dump(include=set(AgentProfileDraft.model_fields)), 'permissions': permission}))
    store.register_instance(host, profile.id)
    second = json.loads(json.dumps(first))
    contract = p['child'].acceptance.model_copy(update={'id': 'added-third-acceptance'})
    oracle = p['source'].parent / 'added-third-oracle/tests'
    oracle.mkdir(parents=True)
    (oracle / 'test_parser.py').write_text('import runpy\nfrom pathlib import Path\ndef test_parse():\n'
        '    assert Path("src/third.py").is_file()\n'
        '    assert runpy.run_path("src/second.py")["value"] == 2\n'
        '    assert runpy.run_path("src/third.py")["value"] == 3\n')
    engine = VerificationEngine(dispatcher.root / 'verification')
    approval = engine.oracles.approve(contract, engine.oracles.read(first['approval_hash']).checks, oracle.parent)
    second['writer'].update(title='Added third child', objective='Create third value 3 after the new second child.',
        acceptance=contract.model_dump(mode='json'), dependencies=['new:0'], permissions=permission.model_dump(mode='json'),
        allowed_profile_ids=[profile.id])
    second['reviewer']['acceptance'] = contract.model_dump(mode='json')
    second['approval_hash'] = approval
    original = model_server.respond
    def respond(request):
        if second['writer']['objective'] not in str(request['messages']):
            return original(request)
        return model_server.completion(json.dumps({'candidates': [{'tool': 'create_file', 'arguments': {
            'path': 'src/third.py', 'content': 'value = 3\n'}}]}))
    model_server.respond = respond
    revised = Pipelines(store, dispatcher.root).replan(owner, recipe.id,
        **addition_arguments(p, recipe, new_branches=[first, second]))
    pairs = [branch for branch in revised.branches if branch.writer_id not in recipe.tasks]
    assert len(pairs) == 2 and revised.tasks[pairs[1].writer_id].dependencies == (pairs[0].writer_id,)
    for number in range(35):
        dispatcher.tick(host, key=f'new-branch-chain-{number}')
        wait_handles(dispatcher)
        if store.workflow(owner, recipe.workflow_id).state == 'succeeded':
            break
    assert store.workflow(owner, recipe.workflow_id).state == 'succeeded'
    assert (p['source'] / 'src/third.py').read_text() == 'value = 3\n'
    assert Integration(store, dispatcher.root).current(owner, store.task(owner, p['parent'].id).result_id)
    assert len(store.attempts(owner, recipe.workflow_id)) == 10


def test_new_pair_cannot_replace_a_required_failed_task(retry_ready, model_server):  # noqa: F811
    p = retry_ready
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    child = p['child']
    p['child'] = store.command(owner, child.id, TaskCommand(operation='revise', expected_revision=child.revision,
        idempotency_key='single-call-failed-child', changes={'budget': BudgetAmount(model_calls=1, tool_calls=1).model_dump()}))
    previous = dispatcher.binding(owner, child.id)
    dispatcher.bind(owner, child.id, p['source'], expected_revision=p['child'].revision,
        expected_binding_id=previous.id, key='bind-single-call-failed-child', brain='heuristic_fsm')
    recipe = approve_parent(p)
    try:
        for number in range(12):
            dispatcher.tick(host, key=f'failed-before-addition-{number}')
            wait_handles(dispatcher)
            if store.task(owner, p['child_review'].id).state == 'failed':
                break
        failed = store.task(owner, p['child'].id)
        assert failed.state == 'awaiting_review'
        objective = 'Independent added parser repair without replacing prior obligations.'
        writer = TaskDraft.model_validate({**p['child'].model_dump(include=set(TaskDraft.model_fields)),
            'title': 'Independent added repair', 'objective': objective, 'budget': BudgetAmount(model_calls=1, tool_calls=1)})
        reviewer = TaskDraft.model_validate({**p['child_review'].model_dump(include=set(TaskDraft.model_fields)),
            'title': 'Review independent repair', 'budget': BudgetAmount(tool_calls=4)})
        p['branch_addition'] = {'writer': writer.model_dump(mode='json'), 'reviewer': reviewer.model_dump(mode='json'),
            'template_writer_id': failed.id, 'approval_hash': p['approvals'][failed.id]}
        original = model_server.respond
        def respond(request):
            if objective not in str(request['messages']):
                return original(request)
            return model_server.completion(json.dumps({'candidates': [{'tool': 'edit_file', 'arguments': {
                'path': 'src/parser.py', 'old_text': 'value = 0', 'new_text': 'value = 1'}}]}))
        model_server.respond = respond
        before = store.budget(owner, recipe.workflow_id)
        revised = Pipelines(store, dispatcher.root).replan(owner, recipe.id, **addition_arguments(p, recipe))
        assert store.task(owner, failed.id) == failed and store.budget(owner, recipe.workflow_id) == before
        pair = next(branch for branch in revised.branches if branch.writer_id not in recipe.tasks)
        for number in range(15):
            dispatcher.tick(host, key=f'failed-after-addition-{number}')
            wait_handles(dispatcher)
            if store.task(owner, pair.writer_id).state == 'succeeded':
                break
        assert store.task(owner, pair.writer_id).state == 'succeeded'
        assert store.task(owner, failed.id) == failed
        assert store.task(owner, p['parent'].id).blocked_reason == 'child_review_failed'
        assert store.workflow(owner, recipe.workflow_id).state != 'succeeded'
        assert sum(attempt.task_id == failed.id for attempt in store.attempts(owner, recipe.workflow_id)) == 1
        assert (p['source'] / 'src/parser.py').read_text() == 'value = 0\n'
    finally:
        for handle in dispatcher.handles.values():
            handle.close()
