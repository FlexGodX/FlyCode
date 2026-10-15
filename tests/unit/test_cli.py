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
