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
