"""FlyCode CLI (plan §89).

SoftwareGym, local models and biological controllers share one agent loop.
Research commands report measured acceptance separately from implementation.
"""

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

from .agent import run_agent
from .brain import make_brain
from .domain import TaskSpec
from .environment import SoftwareEnvironment
from .llm import HeuristicCandidateGenerator, make_lmstudio_cortex, make_openai_llm
from .memory import EpisodicMemory
from .policy import ControlMode
from .telemetry import TelemetryLogger


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _positive_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flycode")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the agent on one task")
    run.add_argument("--repo", type=str, default=None, help="Repository path (created if it doesn't exist)")
    run.add_argument("--fixture", type=str, default=None, help="Deterministic fixture to materialize into --repo")
    run.add_argument("--task", type=str, required=True, help="Task description")
    run.add_argument("--brain", type=str, default="functional", help="Brain backend (see `flycode brains`)")
    run.add_argument("--connectome-path", default=None, help="Prepared sparse graph directory for --brain full")
    run.add_argument("--checkpoint", default=None, help="Offline controller checkpoint directory")
    run.add_argument("--control", type=str, choices=["bias", "hard"], default="bias")
    run.add_argument("--llm", type=str, choices=["heuristic", "openai", "lmstudio"], default="heuristic")
    run.add_argument("--model", type=str, default=None, help="Reasoner: gpt-4o-mini (openai), qwen/qwen3.5-9b (lmstudio)")
    run.add_argument("--decision-model", default="jev-style-qwen3.5-2b-decision", help="LM Studio decision model ID")
    run.add_argument("--base-url", default=None, help="API base URL; local default http://127.0.0.1:1234/v1")
    run.add_argument("--request-timeout", type=_positive_float, default=120.0)
    run.add_argument("--max-model-calls", type=_positive_int, default=60, help="Total local inference request budget")
    run.add_argument("--max-steps", type=int, default=25)
    run.add_argument("--max-tool-calls", type=int, default=60)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--telemetry-dir", type=str, default=None)

    for name, help_text in (
        ("benchmark", "Multi-brain benchmark suite (Phase 11, §61-64)"),
        ("lesion", "Virtual lesion ablation study (Phase 11, §67)"),
        ("sensitivity", "Parameter sensitivity sweep (Phase 11, §68)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(_not_implemented=help_text)

    train = sub.add_parser("train", help="Offline constrained imitation with frozen graph topology")
    train.add_argument("--trajectories", required=True, help="Directory of curated executed *.steps.jsonl episodes")
    train.add_argument("--connectome-path", default=None)
    train.add_argument("--output", required=True)
    train.add_argument("--seed", type=int, default=0)
    train.add_argument("--ridge", type=_positive_float, default=1e-5)

    distill = sub.add_parser("distill", help="Distill trained teacher tool biases into Functional Fly")
    distill.add_argument("--trajectories", required=True)
    distill.add_argument("--connectome-path", default=None)
    distill.add_argument("--teacher-checkpoint", required=True)
    distill.add_argument("--output", required=True)
    distill.add_argument("--seed", type=int, default=0)
    distill.add_argument("--max-rmse", type=float, default=.1)
    distill.add_argument("--min-agreement", type=float, default=.9)

    brains = sub.add_parser("brains", help="List available brain backends")
    brains.set_defaults(_list_brains=True)

    ui = sub.add_parser("ui", help="Open the local browser workspace")
    ui.add_argument("--workspace", default=str(Path.cwd()), help="Directory containing allowed projects")
    ui.add_argument("--port", type=_positive_int, default=8790)
    ui.add_argument("--data-dir", default=None, help="Directory for local run history")

    connectome = sub.add_parser("connectome", help="Prepare or inspect a verified FlyWire graph")
    commands = connectome.add_subparsers(dest="connectome_command", required=True)
    download = commands.add_parser("download")
    download.add_argument("--destination", default=str(Path.home() / ".cache/flycode/flywire-783"))
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source", required=True, help="Directory containing the two official v783 files")
    prepare.add_argument("--output", default=None)
    prepare.add_argument("--min-synapses", type=_positive_int, default=5)
    info = commands.add_parser("info")
    info.add_argument("--path", default=None)
    reduce = commands.add_parser("reduce", help="Extract an induced connected circuit and inherit its mappings")
    reduce.add_argument("--source", required=True, help="Prepared parent graph directory")
    reduce.add_argument("--output", required=True)
    reduce.add_argument("--neurons", type=_positive_int, required=True)
    reduce.add_argument("--checkpoint", default=None, help="Optional trained parent checkpoint")
    reduce.add_argument("--seed", type=int, default=0)

    return parser


async def _run(args: argparse.Namespace) -> int:
    repo = Path(args.repo) if args.repo else Path.cwd() / "flycode_run"
    task = TaskSpec(
        id="cli-task",
        task=args.task,
        fixture=args.fixture,
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        seed=args.seed,
    )

    env = SoftwareEnvironment(repo, seed=args.seed)
    try:
        if args.checkpoint and args.brain not in {"full", "reduced", "functional", "functional_fly"}:
            raise ValueError("This brain does not support checkpoints")
        kwargs = ({"connectome_path": args.connectome_path, "checkpoint_path": args.checkpoint}
                  if args.brain in {"full", "reduced"} else {})
        if args.checkpoint and args.brain in {"functional", "functional_fly"}:
            kwargs = {"checkpoint_path": args.checkpoint}
        brain = make_brain(args.brain, seed=args.seed, **kwargs)
    except ValueError as exc:
        print(f"Brain setup failed: {exc}", file=sys.stderr)
        return 2
    if args.llm == "heuristic":
        cortex = HeuristicCandidateGenerator()
    elif args.llm == "lmstudio":
        from openai import APIError

        try:
            cortex = make_lmstudio_cortex(
                model=args.model or "qwen/qwen3.5-9b", decision_model=args.decision_model,
                base_url=args.base_url, timeout=args.request_timeout, max_model_calls=args.max_model_calls,
            )
        except (APIError, ValueError) as exc:
            print(f"LM Studio setup failed: {exc}", file=sys.stderr)
            return 2
    else:
        cortex = make_openai_llm(model=args.model or "gpt-4o-mini", base_url=args.base_url,
                                 timeout=args.request_timeout)
    control_mode = ControlMode.HARD_CONTROL if args.control == "hard" else ControlMode.BIAS_CONTROL
    telemetry = TelemetryLogger(Path(args.telemetry_dir)) if args.telemetry_dir else None

    result = await run_agent(
        env,
        brain,
        cortex,
        task,
        control_mode=control_mode,
        episodic_memory=EpisodicMemory(),
        telemetry=telemetry,
    )

    print(f"success={result.success} steps={result.steps} tool_calls={result.tool_calls}")
    print(f"final_result: {result.episode.final_result}")
    if result.metrics.model_calls:
        print(f"model_calls: {result.metrics.model_calls}")
    for line in result.transcript:
        print(f"  {line}")
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "_list_brains", False):
        from .brain import BRAIN_REGISTRY

        for name in sorted(BRAIN_REGISTRY):
            print(name)
        return 0

    if getattr(args, "_not_implemented", None):
        print(f"`flycode {args.command}` is not implemented yet: {args._not_implemented}", file=sys.stderr)
        return 2

    if args.command == "run":
        return asyncio.run(_run(args))

    if args.command == "train":
        from .brain.full_connectome.sparse_backend import DEFAULT_CONNECTOME
        from .training.constrained import train_connectome
        from .training.trajectories import load_episodes

        try:
            episodes = load_episodes(Path(args.trajectories))
            report = train_connectome(Path(args.connectome_path) if args.connectome_path else DEFAULT_CONNECTOME,
                                       episodes, Path(args.output), seed=args.seed, ridge=args.ridge)
            print(json.dumps(report, indent=2))
            return 0
        except (OSError, ValueError) as exc:
            print(f"Offline training failed: {exc}", file=sys.stderr)
            return 2

    if args.command == "distill":
        from .brain.full_connectome.sparse_backend import DEFAULT_CONNECTOME
        from .training.distillation import distill_controller
        from .training.trajectories import load_episodes

        try:
            episodes = load_episodes(Path(args.trajectories))
            report = distill_controller(Path(args.connectome_path) if args.connectome_path else DEFAULT_CONNECTOME,
                                        Path(args.teacher_checkpoint), episodes, Path(args.output),
                                        max_rmse=args.max_rmse, min_agreement=args.min_agreement, seed=args.seed)
            print(json.dumps(report, indent=2))
            return 0 if report['accepted'] else 1
        except (OSError, ValueError) as exc:
            print(f"Distillation failed: {exc}", file=sys.stderr)
            return 2

    if args.command == "connectome":
        from .brain.full_connectome.graph import SparseConnectome
        from .brain.full_connectome.importer import download_flywire, prepare_flywire
        from .brain.full_connectome.sparse_backend import DEFAULT_CONNECTOME

        try:
            if args.connectome_command == "reduce":
                from .brain.full_connectome.reduction import reduce_connectome

                report = reduce_connectome(Path(args.source), Path(args.output), args.neurons, seed=args.seed,
                                           checkpoint_path=Path(args.checkpoint) if args.checkpoint else None)
                print(json.dumps(report, indent=2))
                return 0
            if args.connectome_command == "download":
                download_flywire(Path(args.destination))
                print(f"Verified FlyWire 783 source files: {args.destination}")
                return 0
            if args.connectome_command == "prepare":
                graph = prepare_flywire(Path(args.source), Path(args.output) if args.output else DEFAULT_CONNECTOME,
                                        min_synapses=args.min_synapses)
            else:
                graph = SparseConnectome.load(Path(args.path) if args.path else DEFAULT_CONNECTOME)
            print(json.dumps({**graph.metadata, "neurons": len(graph.neuron_ids), "edges": graph.weights.nnz,
                              "fingerprint": graph.fingerprint}, indent=2))
            return 0
        except (OSError, ValueError, ImportError) as exc:
            print(f"Connectome setup failed: {exc}", file=sys.stderr)
            return 2

    if args.command == "ui":
        import uvicorn

        from .web.app import create_app

        if args.port > 65535:
            parser.error("port must be between 1 and 65535")
        print(f"FlyCode: http://127.0.0.1:{args.port}")
        uvicorn.run(create_app(Path(args.workspace), Path(args.data_dir) if args.data_dir else None),
                    host="127.0.0.1", port=args.port)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
