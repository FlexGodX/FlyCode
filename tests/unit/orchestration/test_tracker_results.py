"""Canonical cards separate verified task output, parent wait and applied results."""

import os
import subprocess
from pathlib import Path

import pytest
from test_application_coordinator import native_app
from test_managed_coordinator import managed_pipeline  # noqa: F401
from test_parent_continuation import (  # noqa: F401
    approve_parent,
    parent_ready,
    review_ready,
    wait_handles,
)
from test_pipeline_retries import retry_ready  # noqa: F401
from test_store import queued, setup  # noqa: F401
from test_store import request as claim_request
from test_tracker_cli import cli

from flycode.domain.orchestration import AccessScope
from flycode.orchestration.artifacts import Artifacts
from flycode.orchestration.dispatch import Dispatcher
from flycode.orchestration.host import Host
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.store import StoreError
from flycode.tracker.service import TrackerService
from flycode.verification.engine import VerificationEngine


@pytest.fixture
def pipeline_ready(managed_pipeline):  # noqa: F811
    store, owner, host, workflow, tasks, source, _, runtime_root, oracle, checks, _ = managed_pipeline
    root, writer, reviewer, integrator = tasks
    approval = VerificationEngine(runtime_root / 'verification').oracles.approve(root.acceptance, checks, oracle)
    return store, owner, host, workflow, root, writer, reviewer, integrator, Dispatcher(store, runtime_root), source, approval


@pytest.fixture
def live_parent(parent_ready, managed_pipeline, monkeypatch, request):  # noqa: F811
    if getattr(request, 'param', None) == 'retry':
        parent_ready = request.getfixturevalue('retry_ready')
    store, _, _, _, _, _, host_root, runtime_root, *_ = managed_pipeline
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(store.path))
    with Host(store.path, host_root) as leader:
        connection = leader.connect(catalogue=store.catalogue)
        dispatcher = Dispatcher(connection, runtime_root)
        try:
            yield {**parent_ready, 'store': connection, 'dispatcher': dispatcher}
        finally:
            for handle in dispatcher.handles.values():
                handle.close()
            connection.close()


@pytest.mark.parametrize('stale', ['source', 'checkpoint'])
def test_common_cards_follow_wait_child_proof_integration_and_invalidation(live_parent, stale):
    p = live_parent
    store, owner, host, dispatcher, source = (p[key] for key in ('store', 'owner', 'host', 'dispatcher', 'source'))
    recipe = approve_parent(p)
    tracker = TrackerService(store)
    try:
        first = dispatcher.tick(host, key='projection-prepare')
        wait_handles(dispatcher)
        Pipelines(store, dispatcher.root).advance(host, p['workflow'].id)
        card = tracker.task(owner, p['parent'].id)
        assert card.wait is not None and card.wait.children == (p['child'].id,)
        assert card.wait.pending == (p['child'].id,) and card.wait.checkpoint_id
        assert card.completion is None and card.integration is None
        assert card.lineage.pipeline_id == recipe.id and card.lineage.graph_revision == 0
        assert card.lineage.artifact_id == Artifacts(store, dispatcher.root).for_attempt(owner, first.attempt.id).id
        assert 'waiting_children' in card.blockers
        for number in range(8):
            dispatcher.tick(host, key=f'projection-child-{number}')
            wait_handles(dispatcher)
            if store.task(owner, p['child'].id).completion_id:
                break
        child = tracker.task(owner, p['child'].id)
        assert child.completion is not None and child.completion.current
        assert child.completion.id == child.task.completion_id
        assert child.completion.artifact_id == child.lineage.artifact_id
        assert child.review.status == 'approved' and child.review.evidence_id
        assert child.integration is None, 'Verified child output is not applied workflow integration'
        assert store.workflow(owner, p['workflow'].id).state == 'running'
        assert (source / 'src/parser.py').read_text() == 'value = 0\n'
        child_proof = child.completion.id
        page = tracker.page(owner, limit=100)
        assert next(item for item in page.items if item.task.id == p['child'].id) == child
        denied = AccessScope(actor_id='foreign', project_ids={'elsewhere'})
        with pytest.raises(StoreError, match='scope'):
            tracker.task(denied, p['child'].id)
        assert tracker.page(denied).items == []
        for number in range(12):
            dispatcher.tick(host, key=f'projection-finish-{number}')
            wait_handles(dispatcher)
            if store.workflow(owner, p['workflow'].id).state == 'succeeded':
                break
        parent = tracker.task(owner, p['parent'].id)
        assert parent.completion.current and parent.integration.current
        assert parent.integration.state == 'completed'
        assert parent.integration.id == parent.task.result_id
        assert parent.lineage.inputs[p['child'].id].id == child_proof
        assert parent.lineage.continuation_id == card.wait.checkpoint_id
        assert parent.wait is None, 'Resumed parent must not retain an active child wait'
        assert parent.last_action is not None
        assert all(item.integration.current for item in tracker.board(owner, p['workflow'].id))
        before = store.budget(owner, p['workflow'].id)
        if stale == 'source':
            (source / 'dirty.bin').write_bytes(b'owner changed bytes')
        else:
            (Artifacts(store, dispatcher.root).directory / card.lineage.artifact_id / 'manifest.json').unlink()
        changed = tracker.task(owner, p['parent'].id)
        assert not changed.integration.current and changed.integration.state == 'invalidated'
        assert not changed.completion.current
        assert changed.wait is None and changed.lineage.continuation_id == card.wait.checkpoint_id
        assert changed.task.state != 'succeeded' and changed.blockers
        assert store.budget(owner, p['workflow'].id).spent == before.spent
    finally:
        for handle in dispatcher.handles.values():
            handle.close()


def browser_stage(client, task_id, stage, tmp_path):
    if not os.environ.get('FLYCODE_TASK_RESULT_BROWSER'):
        return
    output = tmp_path / ('browser-' + stage)
    output.mkdir()
    environment = {**os.environ, 'FLYCODE_UI_URL': str(client.base_url).rstrip('/'),
        'FLYCODE_RESULT_TASK': task_id, 'FLYCODE_RESULT_STAGE': stage, 'FLYCODE_SCREENSHOTS': str(output)}
    result = subprocess.run([os.environ.get('FLYCODE_NODE', 'node'),
        str(Path(__file__).parents[2] / 'browser/task-results.cjs')], env=environment,
        capture_output=True, text=True, timeout=60, check=False)
    (output / 'browser.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_cli_http_and_optional_browser_share_native_result_states(live_parent, tmp_path):
    p = live_parent
    store, owner, host, dispatcher, source = (p[key] for key in ('store', 'owner', 'host', 'dispatcher', 'source'))
    approve_parent(p)
    with native_app(source, tmp_path / 'history') as (_, client):
        assert client.post('/api/tasks/projects', json={'selector': '.'}).status_code == 201
        cli('register', '--project', source)
        def same(identity):
            card = TrackerService(store).task(owner, identity).model_dump(mode='json')
            shown = cli('show', identity, '--project', source)
            assert shown == card
            response = client.get('/api/tasks/' + identity)
            assert response.status_code == 200, response.text
            assert response.json() == card
            listed = cli('list', '--project', source, '--limit', '100')
            assert next(item for item in listed['items'] if item['task']['id'] == identity) == card
            page = client.get('/api/tasks?limit=100').json()
            assert next(item for item in page['items'] if item['task']['id'] == identity) == card
            return card
        browser_stage(client, p['child'].id, 'pending', tmp_path)
        dispatcher.tick(host, key='public-view-prepare')
        wait_handles(dispatcher)
        Pipelines(store, dispatcher.root).advance(host, p['workflow'].id)
        wait = same(p['parent'].id)
        assert wait['wait']['pending'] == [p['child'].id]
        browser_stage(client, p['parent'].id, 'wait', tmp_path)
        for number in range(8):
            dispatcher.tick(host, key=f'public-view-child-{number}')
            wait_handles(dispatcher)
            if store.task(owner, p['child'].id).completion_id:
                break
        child = same(p['child'].id)
        assert child['completion']['current'] and child['integration'] is None
        browser_stage(client, p['child'].id, 'child', tmp_path)
        for number in range(12):
            dispatcher.tick(host, key=f'public-view-final-{number}')
            wait_handles(dispatcher)
            if store.workflow(owner, p['workflow'].id).state == 'succeeded':
                break
        parent = same(p['parent'].id)
        assert parent['completion']['current'] and parent['integration']['current']
        browser_stage(client, p['parent'].id, 'integrated', tmp_path)
        before = store.budget(owner, p['workflow'].id)
        (source / 'dirty.bin').write_bytes(b'owner edited after integration')
        stale = same(p['parent'].id)
        assert not stale['integration']['current'] and not stale['completion']['current']
        browser_stage(client, p['parent'].id, 'stale', tmp_path)
        assert store.budget(owner, p['workflow'].id).spent == before.spent


def test_last_action_ignores_actual_heartbeats_and_agents_share_card(setup):  # noqa: F811
    store, owner, host, _, instance, _, _, _ = setup
    _workflow, task = queued(setup)
    claimed = store.claim(host, claim_request(instance, task))
    store.confirm_started(host, claimed.attempt.id, claimed.attempt.fence)
    before = TrackerService(store).task(owner, task.id)
    store.heartbeat(host, claimed.attempt.id, claimed.attempt.fence)
    after = TrackerService(store).task(owner, task.id)
    assert after.last_event.kind == 'attempt_heartbeat'
    assert after.last_action == before.last_action and after.last_action.kind != 'attempt_heartbeat'
    entry = next(item for item in TrackerService(store).agents(owner) if item.task and item.task.id == task.id)
    assert entry.card == after
    assert entry.card.attempt.id == claimed.attempt.id


def test_addressed_workflow_replan_is_visible_in_task_history_and_lineage(live_parent):
    p = live_parent
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    recipe = approve_parent(p)
    dispatcher.tick(host, key='history-prepare')
    wait_handles(dispatcher)
    service = Pipelines(store, dispatcher.root)
    service.advance(host, p['workflow'].id)
    task = store.task(owner, p['child'].id)
    service.replan(owner, recipe.id, changes={task.id: {'objective': 'Repair the parser using the approved contract.'}},
        expected_revision=store.workflow(owner, task.workflow_id).revision,
        expected_revisions={item.id: item.revision for item in store.tasks(owner, task.workflow_id)}, key='history-replan')
    tracker = TrackerService(store)
    card = tracker.task(owner, task.id)
    assert card.lineage.graph_revision == 1 and card.last_action.kind == 'pipeline_replanned'
    events = tracker.task_events(owner, task.id).items
    assert any(event.kind == 'pipeline_replanned' for event in events)
    foreign = AccessScope(actor_id='foreign', project_ids={'elsewhere'})
    with pytest.raises(StoreError, match='scope'):
        tracker.task_events(foreign, task.id)


def test_page_action_metadata_never_escapes_its_event_cursor(setup, monkeypatch):  # noqa: F811
    from flycode.domain.orchestration import TaskDraft, WorkflowDraft
    from flycode.tracker.commands import CommentCommand
    from flycode.tracker.results import ResultViews

    store, owner, *_ = setup
    workflow = store.create_workflow(owner, WorkflowDraft(project_id='p', task=TaskDraft(title='Snapshot draft')), key='snapshot-draft')
    tracker = TrackerService(store)
    inspect = ResultViews.card
    def publish_after_snapshot(views, scope, card):
        tracker.comment(owner, workflow.root_task_id, CommentCommand(text='A later action', idempotency_key='after-snapshot'))
        return inspect(views, scope, card)
    monkeypatch.setattr(ResultViews, 'card', publish_after_snapshot)
    page = tracker.page(owner)
    assert len(page.items) == 1 and page.items[0].last_action.sequence <= page.cursor
    assert page.items[0].last_action.kind == 'workflow_created'
    events, _ = tracker.events(owner, after=page.cursor)
    assert any(event.data.get('text') == 'A later action' for event in events)


def test_reviewer_card_references_its_real_author_artifact_and_evidence(live_parent):
    p = live_parent
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    approve_parent(p)
    for number in range(8):
        dispatcher.tick(host, key=f'reviewer-view-{number}')
        wait_handles(dispatcher)
        if store.task(owner, p['child'].id).completion_id:
            break
    tracker = TrackerService(store)
    child = tracker.task(owner, p['child'].id)
    reviewer = tracker.task(owner, p['child_review'].id)
    assert child.completion.current
    assert reviewer.review == child.review and reviewer.review.status == 'approved'
    assert reviewer.lineage.artifact_id == child.completion.artifact_id
    assert reviewer.completion is None and reviewer.integration is None


def test_changed_card_revision_during_evidence_read_cannot_claim_current(live_parent, monkeypatch):
    from flycode.domain.orchestration import TaskCommand
    from flycode.tracker.results import ResultViews

    p = live_parent
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    approve_parent(p)
    for number in range(8):
        dispatcher.tick(host, key=f'revision-view-{number}')
        wait_handles(dispatcher)
        if store.task(owner, p['child'].id).completion_id:
            break
    tracker = TrackerService(store)
    before = tracker.task(owner, p['child'].id)
    assert before.completion.current
    inspect = ResultViews.card
    def change_after_snapshot(views, scope, card):
        store.command(owner, card.task.id, TaskCommand(operation='set_metadata', expected_revision=card.task.revision,
            idempotency_key='after-result-snapshot', changes={'title': 'Owner renamed after snapshot'}))
        return inspect(views, scope, card)
    monkeypatch.setattr(ResultViews, 'card', change_after_snapshot)
    stale = tracker.task(owner, p['child'].id)
    assert stale.task.title == before.task.title and stale.task.revision == before.task.revision
    assert not stale.completion.current and 'revision' in stale.blockers
    monkeypatch.setattr(ResultViews, 'card', inspect)
    latest = tracker.task(owner, p['child'].id)
    assert latest.task.title == 'Owner renamed after snapshot' and latest.completion.current


@pytest.mark.parametrize('live_parent', ['retry'], indirect=True)
def test_retry_grant_and_saved_reset_are_visible_before_next_writer_starts(live_parent):
    p = live_parent
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    approve_parent(p, retry_policies=p['retry_policies'])
    for number in range(6):
        dispatcher.tick(host, key=f'retry-view-{number}')
        wait_handles(dispatcher)
        if store.task(owner, p['child_review'].id).state == 'failed':
            break
    assert store.task(owner, p['child_review'].id).state == 'failed'
    Pipelines(store, dispatcher.root).advance(host, p['workflow'].id)
    assert store.task(owner, p['child'].id).current_attempt_id is None
    child = TrackerService(store).task(owner, p['child'].id)
    reviewer = TrackerService(store).task(owner, p['child_review'].id)
    assert child.completion is None and child.review.status == 'changes_requested'
    assert child.lineage.retry_number == 1 and child.lineage.retry_limit == 1
    assert reviewer.lineage.retry_number == 1 and reviewer.lineage.retry_limit == 1
    assert len([attempt for attempt in store.attempts(owner, p['workflow'].id) if attempt.task_id == p['child'].id]) == 1


def test_child_card_explains_host_pipeline_gate_before_parent_checkpoint(live_parent):
    p = live_parent
    approve_parent(p)
    tracker = TrackerService(p['store'])
    child = tracker.task(p['owner'], p['child'].id)
    assert child.task.state == 'planned' and child.attempt is None
    assert 'pipeline_branch_paused' in child.blockers
    assert not p['store'].attempts(p['owner'], p['workflow'].id)


def test_last_action_summary_does_not_copy_private_binding_paths(live_parent):
    p = live_parent
    card = TrackerService(p['store']).task(p['owner'], p['child'].id)
    assert card.last_action.kind == 'worker_binding_created'
    assert str(p['source']) not in card.last_action.model_dump_json()
    assert card.last_event.data['binding']['source_root'] == str(p['source'])


@pytest.mark.parametrize('target', ['child', 'integrator'])
def test_later_card_cannot_reuse_integration_pass_after_evidence_disappears(live_parent, monkeypatch, target):
    from flycode.domain.results import ReviewRecord
    from flycode.orchestration.completions import Completions

    p = live_parent
    store, owner, host, dispatcher = (p[key] for key in ('store', 'owner', 'host', 'dispatcher'))
    approve_parent(p)
    for number in range(18):
        dispatcher.tick(host, key=f'cache-view-{number}')
        wait_handles(dispatcher)
        if store.workflow(owner, p['workflow'].id).state == 'succeeded':
            break
    assert store.workflow(owner, p['workflow'].id).state == 'succeeded'
    tracker = TrackerService(store)
    child = tracker.task(owner, p['child'].id)
    assert child.completion.current and child.integration.current
    review = store._load('reviews', child.completion.review_id, ReviewRecord)
    path = dispatcher.root / 'verification' / review.verdict.evidence_id / 'manifest.json'
    current = Completions.current
    def lose_later_evidence(service, scope, identity):
        if identity == child.completion.id and path.exists():
            path.unlink()
        return current(service, scope, identity)
    if target == 'child':
        monkeypatch.setattr(Completions, 'current', lose_later_evidence)
    else:
        from flycode.tracker.results import ResultViews

        inspect = ResultViews.card
        def lose_integration_evidence(views, scope, card):
            if card.task.id == p['integrator'].id and path.exists():
                path.unlink()
            return inspect(views, scope, card)
        monkeypatch.setattr(ResultViews, 'card', lose_integration_evidence)
    cards = tracker.board(owner, p['workflow'].id)
    later = next(card for card in cards if card.task.id == p[target].id)
    assert (later.completion is None if target == 'integrator' else not later.completion.current)
    assert not later.integration.current and later.integration.state == 'completed'
    assert 'result_invalidated' in later.blockers
