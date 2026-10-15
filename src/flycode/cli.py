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
    run.add_argument("--learn", action="store_true", help="Enable bounded Functional Fly online learning")
    run.add_argument("--learning-memory", default=None, help="Load/save learned Functional Fly policy in this directory")
    run.add_argument("--shadow-brain", default=None, help="Zero-influence observer backend")
    run.add_argument("--shadow-connectome-path", default=None, help="Prepared observer graph directory")
    run.add_argument("--shadow-checkpoint", default=None, help="Observer checkpoint directory")
    run.add_argument("--influence", type=float, choices=[0., .1, .25, .5, 1.], default=0.,
                     help="Bounded observer influence on diagnostic choices; requires validation")
    run.add_argument("--validation-report", default=None, help="Scoped admission JSON from validate-rollout")
    run.add_argument("--autonomous", action="store_true", help="Validated guarded hard selection; sets --control hard")
    run.add_argument("--control", type=str, choices=["bias", "hard"], default="bias")
    run.add_argument("--llm", type=str, choices=["heuristic", "openai", "lmstudio"], default="heuristic")
    run.add_argument("--model", type=str, default=None, help="Reasoner: gpt-4o-mini (openai), qwen/qwen3.5-9b (lmstudio)")
    decision = run.add_mutually_exclusive_group()
    decision.add_argument("--decision-model", default="jev-style-qwen3.5-2b-decision", help="LM Studio decision model ID")
    decision.add_argument("--no-decision-model", dest="decision_model", action="store_const", const=None,
                          help="Use only the primary model, without an additional scorer")
    run.add_argument("--base-url", default=None, help="API base URL; local default http://127.0.0.1:1234/v1")
    run.add_argument("--request-timeout", type=_positive_float, default=120.0)
    run.add_argument("--max-model-calls", type=_positive_int, default=60, help="Total local inference request budget")
    run.add_argument("--max-steps", type=int, default=25)
    run.add_argument("--max-tool-calls", type=int, default=60)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--telemetry-dir", type=str, default=None)

    for name in ("lesion", "sensitivity"):
        probe = sub.add_parser(name, help="Recorded-trajectory causal probe; no task-success estimate")
        probe.add_argument("--trajectories", required=True)
        probe.add_argument("--connectome-path", default=None)
        probe.add_argument("--checkpoint", default=None)
        probe.add_argument("--seed", type=int, default=0)
        probe.add_argument("--output", required=True, help="JSON report file")
        if name == "lesion":
            probe.add_argument("--fraction", type=float, default=.1)
        else:
            probe.add_argument("--parameter", choices=["recurrent_gain", "leak", "input_gain"], required=True)
            probe.add_argument("--values", type=float, nargs="+", required=True)

    benchmark = sub.add_parser("benchmark", help="Isolated multi-seed controller/checkpoint task comparison")
    benchmark.add_argument("--config", required=True, help="JSON BenchmarkConfig")
    benchmark.add_argument("--output", required=True, help="Directory for experiments and recorded trajectories")

    adapt = sub.add_parser("adapt", help="Repeated task families with retained learning and frozen controls")
    adapt.add_argument("--config", required=True, help="JSON AdaptationConfig")
    adapt.add_argument("--output", required=True, help="Directory for results, trajectories and learned memory")

    shadow_analysis = sub.add_parser("shadow-analysis", help="Analyze observed recommendations without causal success claims")
    shadow_analysis.add_argument("--trajectories", required=True)
    shadow_analysis.add_argument("--output", required=True)

    validate = sub.add_parser("validate-rollout", help="Derive scoped admission from a paired experiment report")
    validate.add_argument("--report", required=True)
    validate.add_argument("--controller", required=True)
    validate.add_argument("--baseline", required=True)
    validate.add_argument("--mode", choices=["bounded", "autonomous"], default="bounded")
    validate.add_argument("--output", required=True)

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
    admission = None
    try:
        if args.autonomous:
            if (not args.validation_report or args.influence or args.learn or args.learning_memory
                    or args.llm not in {"heuristic", "lmstudio"}):
                raise ValueError("Autonomy requires matching admission, frozen policy and a measured cortex")
            args.control = "hard"
        if args.influence:
            if not args.shadow_brain or not args.validation_report:
                raise ValueError("Nonzero influence requires --shadow-brain and --validation-report")
            if args.control != "bias" or args.learn or args.learning_memory or args.llm not in {"heuristic", "lmstudio"}:
                raise ValueError("Bounded admission requires frozen bias control with a measured heuristic/local cortex")
        elif args.validation_report and not args.autonomous:
            raise ValueError("An admission report requires bounded influence or autonomous execution")
        if (args.learn or args.learning_memory) and args.brain not in {"functional", "functional_fly"}:
            raise ValueError("Online learning currently requires Functional Fly")
        if args.checkpoint and args.brain not in {"full", "reduced", "functional", "functional_fly"}:
            raise ValueError("This brain does not support checkpoints")
        kwargs = ({"connectome_path": args.connectome_path, "checkpoint_path": args.checkpoint}
                  if args.brain in {"full", "reduced"} else {})
        if args.checkpoint and args.brain in {"functional", "functional_fly"}:
            kwargs = {"checkpoint_path": args.checkpoint}
        brain = make_brain(args.brain, seed=args.seed, **kwargs)
        if args.learning_memory:
            directory = Path(args.learning_memory).expanduser()
            if directory.exists() and not directory.is_dir():
                raise ValueError("Learning memory must be a directory")
            if (directory / "learning.json").exists():
                assert hasattr(brain, "load_learning")
                brain.load_learning(directory)
        brain.set_learning_mode(bool(args.learn or args.learning_memory))
        if not args.shadow_brain and (args.shadow_connectome_path or args.shadow_checkpoint):
            raise ValueError("Observer paths require --shadow-brain")
        if args.shadow_brain:
            from .brain.shadow import ShadowBackend

            if args.shadow_connectome_path and args.shadow_brain not in {"full", "reduced"}:
                raise ValueError("Observer graph path requires full or reduced brain")
            if args.shadow_checkpoint and args.shadow_brain not in {"full", "reduced", "functional", "functional_fly"}:
                raise ValueError("This observer does not support checkpoints")
            observer_kwargs = ({"connectome_path": args.shadow_connectome_path, "checkpoint_path": args.shadow_checkpoint}
                               if args.shadow_brain in {"full", "reduced"} else {})
            if args.shadow_checkpoint and args.shadow_brain in {"functional", "functional_fly"}:
                observer_kwargs = {"checkpoint_path": args.shadow_checkpoint}
            brain = ShadowBackend(brain, make_brain(args.shadow_brain, seed=args.seed, **observer_kwargs),
                                  influence=args.influence)
        if args.influence or args.autonomous:
            from .research.benchmark import BrainSpec, ControllerSpec
            from .research.cortex import CortexConfig
            from .research.validation import authorize

            spec = ControllerSpec(id="runtime", brain=args.brain,
                connectome_path=args.connectome_path if args.brain in {"full", "reduced"} else None,
                checkpoint=args.checkpoint, observer=(BrainSpec(brain=args.shadow_brain,
                    connectome_path=args.shadow_connectome_path, checkpoint=args.shadow_checkpoint)
                    if args.shadow_brain else None), influence=args.influence, autonomous=args.autonomous)
            admission = authorize(Path(args.validation_report).expanduser(), identity=spec.identity(brain),
                config=CortexConfig(cortex=args.llm, model=args.model or "qwen/qwen3.5-9b",
                    decision_model=args.decision_model, base_url=args.base_url,
                    request_timeout=args.request_timeout, max_model_calls=args.max_model_calls),
                fixture=args.fixture, seed=args.seed, control=args.control, max_steps=args.max_steps,
                max_tool_calls=args.max_tool_calls, mode="autonomous" if args.autonomous else "bounded",
                influence=1. if args.autonomous else args.influence, task=args.task)
    except (OSError, ValueError, TypeError) as exc:
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
    if admission is not None:
        print("admission: matching controller/task/model/budget evidence accepted")
        if telemetry is not None:
            (telemetry.directory / "run.admission.json").write_text(json.dumps(admission, indent=2))

    result = await run_agent(
        env,
        brain,
        cortex,
        task,
        control_mode=control_mode,
        episodic_memory=EpisodicMemory(),
        telemetry=telemetry,
        autonomous_guard=args.autonomous,
    )

    print(f"success={result.success} steps={result.steps} tool_calls={result.tool_calls}")
    print(f"final_result: {result.episode.final_result}")
    if result.metrics.model_calls:
        print(f"model_calls: {result.metrics.model_calls}")
    if result.metrics.shadow_decisions:
        print(f"shadow_agreements: {result.metrics.shadow_agreements}/{result.metrics.shadow_decisions} "
              f"requested_influence={args.influence:g}")
    if args.influence:
        print(f"rollout: influence={args.influence} interventions={result.metrics.rollout_interventions} "
              f"rollbacks={result.metrics.rollout_rollbacks}")
    if args.autonomous:
        print(f"autonomy: guarded hard control, rollbacks={result.metrics.autonomy_rollbacks}")
    for line in result.transcript:
        print(f"  {line}")
    if args.learning_memory:
        try:
            assert hasattr(brain, "save_learning")
            brain.save_learning(Path(args.learning_memory).expanduser())
        except OSError as exc:
            print(f"Learning memory could not be saved: {exc}", file=sys.stderr)
            return 2
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "_list_brains", False):
        from .brain import BRAIN_REGISTRY

        for name in sorted(BRAIN_REGISTRY):
            print(name)
        return 0

    if args.command == "run":
        return asyncio.run(_run(args))

    if args.command == "benchmark":
        from .research.benchmark import BenchmarkConfig, run_benchmark

        try:
            config = BenchmarkConfig.model_validate_json(Path(args.config).read_text())
            report = asyncio.run(run_benchmark(config, Path(args.output)))
            print(json.dumps({'experiment_id': report['experiment_id'], 'summary': report['summary']}, indent=2))
            return 1 if any(case['error'] for case in report['cases']) else 0
        except (OSError, ValueError) as exc:
            print(f"Benchmark failed: {exc}", file=sys.stderr)
            return 2

    if args.command == "adapt":
        from .research.adaptation import AdaptationConfig, run_adaptation

        try:
            adaptation_config = AdaptationConfig.model_validate_json(Path(args.config).read_text())
            report = asyncio.run(run_adaptation(adaptation_config, Path(args.output)))
            print(json.dumps({'experiment_id': report['experiment_id'], 'summary': report['summary'],
                              'faster_adaptation_demonstrated': report['faster_adaptation_demonstrated']}, indent=2))
            return 1 if any(case['error'] for case in report['cases']) else 0
        except (OSError, ValueError) as exc:
            print(f"Adaptation experiment failed: {exc}", file=sys.stderr)
            return 2

    if args.command == "shadow-analysis":
        from .research.shadow import save_shadow_analysis

        try:
            report = save_shadow_analysis(Path(args.trajectories), Path(args.output))
            print(json.dumps(report, indent=2, allow_nan=False))
            return 1 if report["invalid_records"] else 0
        except (OSError, ValueError) as exc:
            print(f"Shadow analysis failed: {exc}", file=sys.stderr)
            return 2

    if args.command == "validate-rollout":
        from .research.validation import create_validation

        try:
            report = create_validation(Path(args.report), args.controller, args.baseline,
                                       Path(args.output), mode=args.mode)
            print(json.dumps(report, indent=2, allow_nan=False))
            return 0 if report["accepted"] else 1
        except (OSError, ValueError, TypeError) as exc:
            print(f"Rollout validation failed: {exc}", file=sys.stderr)
            return 2

    if args.command in {"lesion", "sensitivity"}:
        from .brain.full_connectome.sparse_backend import DEFAULT_CONNECTOME
        from .research.perturbation import lesion, sensitivity
        from .training.trajectories import load_episodes

        try:
            episodes = load_episodes(Path(args.trajectories))
            path = Path(args.connectome_path) if args.connectome_path else DEFAULT_CONNECTOME
            checkpoint = Path(args.checkpoint) if args.checkpoint else None
            if args.command == "lesion":
                report = lesion(path, episodes, fraction=args.fraction, seed=args.seed, checkpoint_path=checkpoint)
            else:
                report = sensitivity(path, episodes, parameter=args.parameter, values=args.values,
                                     seed=args.seed, checkpoint_path=checkpoint)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, allow_nan=False))
            print(json.dumps(report, indent=2, allow_nan=False))
            return 0
        except (OSError, ValueError) as exc:
            print(f"Causal probe failed: {exc}", file=sys.stderr)
            return 2

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
