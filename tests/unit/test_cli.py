"""CLI exposes local inference without changing the offline default."""

import pytest

from flycode.cli import _build_parser


def test_lmstudio_cli_accepts_both_models_and_endpoint():
    args = _build_parser().parse_args([
        "run", "--task", "fix", "--llm", "lmstudio", "--base-url", "http://127.0.0.1:1234/v1",
        "--model", "qwen/qwen3.5-9b", "--decision-model", "jev-style-qwen3.5-2b-decision",
        "--max-model-calls", "12", "--request-timeout", "15",
    ])
    assert args.llm == "lmstudio"
    assert args.model == "qwen/qwen3.5-9b"
    assert args.max_model_calls == 12
    assert args.request_timeout == 15


@pytest.mark.parametrize("flag,value", [
    ("--max-model-calls", "0"), ("--max-model-calls", "-1"),
    ("--request-timeout", "0"), ("--request-timeout", "nan"), ("--request-timeout", "inf"),
])
def test_model_request_limits_reject_invalid_values(flag, value):
    with pytest.raises(SystemExit) as error:
        _build_parser().parse_args(["run", "--task", "fix", flag, value])
    assert error.value.code == 2


def test_default_cli_stays_offline():
    args = _build_parser().parse_args(["run", "--task", "fix"])
    assert args.llm == "heuristic"


def test_ui_accepts_workspace_and_port():
    args = _build_parser().parse_args(["ui", "--workspace", "/tmp/projects", "--port", "8787"])
    assert args.workspace == "/tmp/projects"
    assert args.port == 8787


def test_full_brain_and_connectome_preparation_flags():
    args = _build_parser().parse_args(['run', '--task', 'fix', '--brain', 'full', '--connectome-path', '/tmp/graph'])
    assert args.connectome_path == '/tmp/graph'
    prepare = _build_parser().parse_args(['connectome', 'prepare', '--source', '/tmp/raw', '--output', '/tmp/graph'])
    assert prepare.source == '/tmp/raw'
    assert prepare.min_synapses == 5


def test_training_missing_trajectories_fails_explicitly(tmp_path, capsys):
    from flycode.cli import main

    result = main(['train', '--trajectories', str(tmp_path), '--output', str(tmp_path / 'output')])
    assert result == 2
    assert 'trajector' in capsys.readouterr().err.lower()
    assert not (tmp_path / 'output').exists()


def test_run_accepts_explicit_checkpoint():
    args = _build_parser().parse_args(['run', '--task', 'fix', '--brain', 'full', '--checkpoint', '/tmp/trained'])
    assert args.checkpoint == '/tmp/trained'


def test_reduce_command_writes_a_loadable_biological_subcircuit(tmp_path):
    import numpy as np

    from flycode.brain import make_brain
    from flycode.brain.full_connectome.graph import graph_from_edges
    from flycode.cli import main

    path = tmp_path / 'parent'
    graph_from_edges(np.arange(8), np.arange(8), np.roll(np.arange(8), 1), np.ones(8)).save(path)
    output = tmp_path / 'reduced'
    assert main(['connectome', 'reduce', '--source', str(path), '--output', str(output), '--neurons', '4']) == 0
    brain = make_brain('reduced', connectome_path=output)
    assert brain.get_memory_capacity() == 4


def test_distill_reports_missing_recordings_instead_of_stub(tmp_path, capsys):
    from flycode.cli import main

    assert main(['distill', '--trajectories', str(tmp_path), '--teacher-checkpoint', str(tmp_path),
                 '--output', str(tmp_path / 'student')]) == 2
    assert 'trajector' in capsys.readouterr().err.lower()


def test_benchmark_command_saves_configured_results(tmp_path):
    import json

    from flycode.cli import main

    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'controllers': [{'id': 'fsm', 'brain': 'heuristic_fsm'}],
                                 'seeds': [0], 'fixtures': ['toy_arithmetic_bug'],
                                 'controls': ['bias'], 'max_steps': 1}))
    output = tmp_path / 'results'
    assert main(['benchmark', '--config', str(config), '--output', str(output)]) == 0
    reports = list(output.glob('*/report.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report['summary']['fsm']['success_rate'] == 0.


def test_learning_cli_rejects_unsupported_brain_before_running(tmp_path, capsys):
    from flycode.cli import main

    assert main(['run', '--task', 'fix', '--brain', 'full', '--learn', '--repo', str(tmp_path / 'repo')]) == 2
    assert 'Functional Fly' in capsys.readouterr().err
    assert not (tmp_path / 'repo').exists()


def test_causal_probe_commands_write_actual_replay_measurements(tmp_path):
    import json

    import numpy as np

    from flycode.brain.full_connectome.graph import graph_from_edges
    from flycode.cli import main
    from flycode.domain import Observation
    from flycode.telemetry import TrainingStep

    graph = tmp_path / 'graph'
    graph_from_edges(np.arange(8), np.arange(8), np.roll(np.arange(8), 1), np.ones(8)).save(graph)
    records = tmp_path / 'records'
    records.mkdir()
    (records / 'run.steps.jsonl').write_text(TrainingStep(observation=Observation(task='probe').model_dump(),
                                              chosen_action={'tool': 'read_file'}, outcome={}).model_dump_json())
    for command, options in [('lesion', ['--fraction', '1']),
                             ('sensitivity', ['--parameter', 'leak', '--values', '.2', '.3'])]:
        output = tmp_path / (command + '.json')
        assert main([command, '--connectome-path', str(graph), '--trajectories', str(records),
                     '--output', str(output), *options]) == 0
        report = json.loads(output.read_text())
        assert report['counterfactual_task_success'] is None
        assert report['dataset_sha256']


def test_shadow_cli_records_recommendations_without_changing_completion(tmp_path):
    import json

    import numpy as np

    from flycode.brain.full_connectome.graph import graph_from_edges
    from flycode.cli import main

    graph = tmp_path / 'graph'
    graph_from_edges(np.arange(8), np.arange(8), np.roll(np.arange(8), 1), np.ones(8)).save(graph)
    telemetry = tmp_path / 'telemetry'
    assert main(['run', '--task', 'fix', '--repo', str(tmp_path / 'repo'), '--fixture', 'toy_arithmetic_bug',
                 '--shadow-brain', 'full', '--shadow-connectome-path', str(graph),
                 '--telemetry-dir', str(telemetry)]) == 0
    steps = [json.loads(line) for line in (telemetry / 'run.steps.jsonl').read_text().splitlines()]
    assert all(step['shadow_decision']['influence'] == 0. for step in steps)
    assert len(steps) == 9


def test_shadow_cli_rejects_incompatible_paths_before_running(tmp_path, capsys):
    from flycode.cli import main

    for flags in (['--shadow-checkpoint', 'unused'],
                  ['--shadow-brain', 'mock', '--shadow-connectome-path', 'unused'],
                  ['--shadow-brain', 'mock', '--shadow-checkpoint', 'unused']):
        assert main(['run', '--task', 'fix', '--repo', str(tmp_path / 'repo'), *flags]) == 2
        assert 'Brain setup failed' in capsys.readouterr().err
        assert not (tmp_path / 'repo').exists()


def test_adaptation_cli_saves_failed_cases_and_returns_error_status(tmp_path):
    import json

    from flycode.cli import main

    config = tmp_path / 'adapt.json'
    config.write_text(json.dumps({'seeds': [0], 'fixtures': ['toy_import_bug'], 'repeats': 2,
                                 'checkpoint': str(tmp_path / 'missing')}))
    output = tmp_path / 'results'
    assert main(['adapt', '--config', str(config), '--output', str(output)]) == 1
    report = json.loads(next(output.glob('*/report.json')).read_text())
    assert report['summary']['learning']['errors'] == 2


def test_shadow_analysis_cli_keeps_invalid_records_visible(tmp_path):
    import json

    from flycode.cli import main

    records = tmp_path / 'records'
    records.mkdir()
    (records / 'run.steps.jsonl').write_text('{broken')
    output = tmp_path / 'analysis.json'
    assert main(['shadow-analysis', '--trajectories', str(records), '--output', str(output)]) == 1
    assert json.loads(output.read_text())['invalid_records'] == 1


def test_validation_cli_reports_missing_source_without_creating_admission(tmp_path):
    from flycode.cli import main

    output = tmp_path / 'validation.json'
    assert main(['validate-rollout', '--report', str(tmp_path / 'missing'), '--controller', 'target',
                 '--baseline', 'base', '--output', str(output)]) == 2
    assert not output.exists()


def test_nonzero_influence_requires_admission_and_compatible_execution_before_running(tmp_path, capsys):
    from flycode.cli import main

    for flags in [
        ['--influence', '.1'],
        ['--influence', '.1', '--shadow-brain', 'mock'],
        ['--influence', '.1', '--shadow-brain', 'mock', '--control', 'hard'],
        ['--influence', '.1', '--shadow-brain', 'mock', '--learn'],
        ['--validation-report', str(tmp_path / 'missing')],
    ]:
        assert main(['run', '--task', 'fix', '--repo', str(tmp_path / 'repo'), *flags]) == 2
        assert 'Brain setup failed' in capsys.readouterr().err
        assert not (tmp_path / 'repo').exists()


def test_autonomous_mode_requires_evidence_and_frozen_compatible_settings(tmp_path, capsys):
    from flycode.cli import main

    for flags in [[], ['--learn'], ['--influence', '.1', '--shadow-brain', 'mock'],
                  ['--validation-report', str(tmp_path / 'missing')]]:
        assert main(['run', '--task', 'fix', '--autonomous', '--repo', str(tmp_path / 'repo'), *flags]) == 2
        assert 'Brain setup failed' in capsys.readouterr().err
        assert not (tmp_path / 'repo').exists()
