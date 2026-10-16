"""Tracker commands and cards use the same task IDs, revisions and event log."""

import pytest


def test_tracker_is_a_restartable_projection_without_model_or_tool_effects(tmp_path, monkeypatch):
    from flycode.domain.orchestration import AccessScope, TaskDraft, WorkflowDraft
    from flycode.orchestration.store import Store
    from flycode.tracker.service import TrackerService

    effects = []
    monkeypatch.setattr('flycode.llm.make_lmstudio_cortex', lambda **kwargs: effects.append(kwargs))
    scope = AccessScope(actor_id='owner', project_ids={'project'}, role='owner')
    path = tmp_path / 'queue.sqlite'
    store = Store(path)
    tracker = TrackerService(store)
    wf = tracker.create(scope, WorkflowDraft(project_id='project', task=TaskDraft(title='Local draft')), key='draft')
    card = tracker.task(scope, wf.root_task_id)
    assert card.task == store.task(scope, wf.root_task_id)
    assert card.column == 'drafts' and card.attempt is None
    change = {'operation': 'set_metadata', 'expected_revision': card.task.revision,
              'idempotency_key': 'title', 'changes': {'title': 'Renamed', 'tags': ['local']}}
    result = tracker.command(scope, card.task.id, change)
    changed = tracker.task(scope, card.task.id)
    assert changed.task == result
    assert changed.task.revision == card.task.revision + 1
    assert changed.task.semantic_revision == card.task.semantic_revision
    assert changed.last_event.kind == 'task_set_metadata'
    assert tracker.agents(scope) == [] and effects == []
    assert tracker.command(scope, card.task.id, change) == result
    store.close()
    reopened = Store(path)
    try:
        assert TrackerService(reopened).task(scope, card.task.id) == changed
        assert TrackerService(reopened).board(scope, wf.id)[0] == changed
        with pytest.raises(ValueError):
            TrackerService(reopened).command(scope, card.task.id, {'operation': 'set_state', 'state': 'succeeded'})
        assert len(reopened.events(scope, wf.id)) == 2
    finally:
        reopened.close()
