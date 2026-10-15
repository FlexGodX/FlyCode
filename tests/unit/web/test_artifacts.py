"""Browser choices resolve server-owned artifacts and isolated learning profiles."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_app import wait_run

from flycode.brain.full_connectome.graph import graph_from_edges
from flycode.web.app import create_app


def test_catalogue_runs_graph_and_observer_and_rejects_escaped_artifacts(tmp_path):
    root = tmp_path / 'artifacts'
    graph = graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64))
    graph.save(root / 'graph')
    outside = tmp_path / 'outside'
    graph.save(outside)
    (root / 'escape').symlink_to(outside, target_is_directory=True)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    with TestClient(create_app(workspace, tmp_path / 'history', artifact_root=root),
                    base_url='http://localhost') as client:
        bootstrap = client.get('/api/bootstrap').json()
        client.headers['X-FlyCode-Token'] = bootstrap['token']
        controllers = bootstrap['controllers']
        biological = [item for item in controllers if item['id'] not in ('functional', 'full')]
        assert len(biological) == 1
        response = client.post('/api/runs', json={'task': 'fix', 'llm': 'heuristic',
            'controller_id': 'functional', 'observer_id': biological[0]['id']})
        assert response.status_code == 201, response.text
        record = wait_run(client, response.json()['id'])
        assert record['status'] == 'completed', record
        assert record['result']['metrics']['shadow_decisions'] > 0
        for extra in ({'controller_id': '../outside'}, {'observer_id': 'missing'},
                      {'influence': .1}, {'execution_mode': 'autonomous'},
                      {'learning': True, 'memory_profile': '../escape'}):
            response = client.post('/api/runs', json={'task': 'fix', 'llm': 'heuristic', **extra})
            assert response.status_code in (400, 422), response.text
        assert len(client.get('/api/runs').json()) == 1
        # Replacing an artifact's payload with an escaping symlink must fail before a worker starts.
        (root / 'graph' / 'weights.npz').unlink()
        (root / 'graph' / 'weights.npz').symlink_to(outside / 'weights.npz')
        response = client.post('/api/runs', json={'task': 'fix', 'llm': 'heuristic',
            'controller_id': biological[0]['id']})
        assert response.status_code == 400


def test_learning_persists_and_profiles_cannot_escape(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    data = tmp_path / 'history'
    with TestClient(create_app(workspace, data, artifact_root=tmp_path / 'artifacts'),
                    base_url='http://localhost') as client:
        client.headers['X-FlyCode-Token'] = client.get('/api/bootstrap').json()['token']
        for _ in range(2):
            response = client.post('/api/runs', json={'task': 'fix', 'llm': 'heuristic',
                'learning': True, 'memory_profile': 'Учебный'})
            assert response.status_code == 201, response.text
            record = wait_run(client, response.json()['id'])
            assert record['status'] == 'completed', record
        files = list((data / 'memory').glob('*/learning.json'))
        assert len(files) == 1
        assert json.loads(files[0].read_text())['policy']['updates'] > 0
        outside = tmp_path / 'outside'
        outside.mkdir()
        files[0].parent.rename(data / 'backup')
        files[0].parent.symlink_to(outside, target_is_directory=True)
        response = client.post('/api/runs', json={'task': 'fix', 'llm': 'heuristic',
            'learning': True, 'memory_profile': 'Учебный'})
        assert response.status_code == 400
        assert not list(outside.iterdir())


@pytest.mark.parametrize('extra', [
    {'learning': True, 'execution_mode': 'autonomous'},
    {'learning': True, 'influence': .1}, {'influence': .2},
])
def test_invalid_rollout_options_are_rejected_before_worker(tmp_path, extra):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    with TestClient(create_app(workspace, tmp_path / 'history'), base_url='http://localhost') as client:
        client.headers['X-FlyCode-Token'] = client.get('/api/bootstrap').json()['token']
        assert client.post('/api/runs', json={'task': 'fix', **extra}).status_code in (400, 422)
        assert client.get('/api/runs').json() == []


async def test_browser_admission_executes_guarded_scope_and_rejects_project(tmp_path):
    from flycode.research.benchmark import BenchmarkConfig, run_benchmark
    from flycode.research.validation import create_validation

    graph = tmp_path / 'artifacts' / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(graph)
    root = tmp_path / 'artifacts'
    report = await run_benchmark(BenchmarkConfig.model_validate({'controllers': [
        {'id': 'baseline', 'brain': 'functional'},
        {'id': 'guarded', 'brain': 'full', 'connectome_path': str(graph), 'autonomous': True}],
        'controls': ['bias', 'hard']}), root / 'runs')
    artifact = root / 'autonomous-validation.json'
    verdict = create_validation(root / 'runs' / report['experiment_id'] / 'report.json',
                                'guarded', 'baseline', artifact, mode='autonomous')
    assert verdict['accepted'], verdict
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    with TestClient(create_app(workspace, tmp_path / 'history', artifact_root=root),
                    base_url='http://localhost') as client:
        bootstrap = client.get('/api/bootstrap').json()
        client.headers['X-FlyCode-Token'] = bootstrap['token']
        controller = next(item['id'] for item in bootstrap['controllers'] if item['id'] not in ('functional', 'full'))
        admission = bootstrap['admissions'][0]['id']
        body = {'task': 'fix', 'llm': 'heuristic', 'controller_id': controller,
                'execution_mode': 'autonomous', 'admission_id': admission, 'max_tool_calls': 60}
        response = client.post('/api/runs', json=body)
        assert response.status_code == 201, response.text
        record = wait_run(client, response.json()['id'])
        assert record['status'] == 'completed', record
        events = client.get(f"/api/runs/{record['id']}/events").text
        assert '"kind":"autonomy"' in events
        assert '"kind":"admission"' in events
        for extra in ({'project': '.'}, {'seed': 9}, {'max_tool_calls': 59}, {'llm': 'lmstudio'}):
            response = client.post('/api/runs', json={**body, **extra})
            assert response.status_code == 400, response.text
        assert list(workspace.iterdir()) == []


def test_catalogue_executes_reduced_trained_and_distilled_controllers(tmp_path):
    from flycode.brain.full_connectome.reduction import reduce_connectome
    from flycode.telemetry import TrainingStep
    from flycode.training.constrained import train_connectome
    from flycode.training.distillation import distill_controller

    root = tmp_path / 'artifacts'
    graph = root / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(graph)
    reduce_connectome(graph, root / 'reduced', 16, seed=0)
    episode = [TrainingStep(observation={'task': 'fix', 'step_number': index,
        'tests': {'status': 'passed' if index == 2 else 'failed', 'failed': int(index < 2)},
        'modified_files': ['app.py'] if index else []}, chosen_action={'tool': tool}, outcome={'success': True})
        for index, tool in enumerate(['read_file', 'edit_file', 'stop'])]
    train_connectome(graph, [episode, episode], root / 'teacher')
    distilled = distill_controller(graph, root / 'teacher', [episode] * 4, root / 'student',
                                   max_rmse=.05, min_agreement=1.)
    assert distilled['accepted']
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    with TestClient(create_app(workspace, tmp_path / 'history', artifact_root=root),
                    base_url='http://localhost') as client:
        bootstrap = client.get('/api/bootstrap').json()
        client.headers['X-FlyCode-Token'] = bootstrap['token']
        for label, expected in [('Reduced', 'reduced_connectome'), ('Обученный full', 'full_connectome'),
                                ('Functional Fly · student', 'functional_fly')]:
            controller = next(item['id'] for item in bootstrap['controllers'] if item['label'].startswith(label))
            response = client.post('/api/runs', json={'task': 'fix', 'llm': 'heuristic',
                'controller_id': controller, 'max_steps': 2})
            assert response.status_code == 201, response.text
            record = wait_run(client, response.json()['id'])
            assert record['result']['metrics']['brain'] == expected, record
            assert record['request']['controller_id'] == controller
