"""One authority check per atomic dispatch of the owner-approved hierarchy."""

import runpy
from pathlib import Path

import pytest
from test_managed_coordinator import managed_pipeline  # noqa: F401

from flycode.orchestration.host import Host
from flycode.orchestration.pipeline import Pipelines
from flycode.orchestration.store import changed


@pytest.fixture
def model_server(model_server):
    model_server.contexts = {name: 16384 for name in model_server.models}
    return model_server


@pytest.mark.parametrize('mutation', [None, 'semantic', 'binding'])
def test_hierarchy_dispatch_checks_graph_once_per_writer_transaction(managed_pipeline, model_server, tmp_path, monkeypatch, mutation):  # noqa: F811
    setup = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'browser/saturation_scenario.py'))['prepare']
    with Host(managed_pipeline[0].path, managed_pipeline[6]) as host:
        store = host.connect(catalogue=managed_pipeline[0].catalogue)
        try:
            prepared = setup(managed_pipeline, store, model_server, tmp_path, resource_pressure=True)
            owner, runtime, workflow = (prepared[key] for key in ('owner', 'runtime', 'workflow'))
            assert Pipelines(store, prepared['dispatcher'].root).advance(runtime, workflow.id) == ()
            assert store.workflow(owner, workflow.id).state == 'running'
            if mutation == 'semantic':
                task = store.task(owner, prepared['parent'].id)
                store._save_task(changed(task, semantic_revision=task.semantic_revision + 1))
            elif mutation == 'binding':
                store.db.execute('UPDATE worker_bindings SET current=0 WHERE task_id=?', (prepared['parent'].id,))
            checked = []
            guard = Pipelines.guard_metadata
            def count_guard(connection, scope, recipe):
                if connection is store and connection.db.in_transaction:
                    checked.append(recipe.id)
                return guard(connection, scope, recipe)
            monkeypatch.setattr(Pipelines, 'guard_metadata', staticmethod(count_guard))
            bindings = tuple(row['id'] for row in store.db.execute('SELECT id FROM worker_bindings WHERE current=1'))
            result = store.dispatch_bound(runtime, runtime_root=str(prepared['dispatcher'].root),
                binding_ids=bindings, key='hierarchy-atomic-selection')
            assert checked == [prepared['recipe'].id]
            if mutation is None:
                assert result.attempt is not None and result.attempt.task_id == prepared['parent'].id
            else:
                assert result.attempt is None
                assert ('pipeline_changed' if mutation == 'semantic' else 'binding_revision') in result.reasons
            assert len(model_server.requests) == 1
            assert store.budget(owner, workflow.id).spent.model_calls == 0
            assert store.budget(owner, workflow.id).spent.tool_calls == 0
        finally:
            store.close()
