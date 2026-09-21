"""FlyCode CLI (plan §89).

`flycode run` is fully implemented (Phase 0-3: SoftwareGym + baselines +
Functional Fly). `train`/`distill`/`benchmark`/`lesion`/`sensitivity` are
later-phase commands (§85-88) and intentionally exit with a clear
"not implemented yet" message rather than pretending to do something.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from .agent import run_agent
from .brain import make_brain
from .domain import TaskSpec
from .environment import SoftwareEnvironment
from .llm import HeuristicCandidateGenerator, make_openai_llm
from .memory import EpisodicMemory
from .policy import ControlMode
from .telemetry import TelemetryLogger


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flycode")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the agent on one task")
    run.add_argument("--repo", type=str, default=None, help="Repository path (created if it doesn't exist)")
    run.add_argument("--fixture", type=str, default=None, help="Deterministic fixture to materialize into --repo")
    run.add_argument("--task", type=str, required=True, help="Task description")
    run.add_argument("--brain", type=str, default="functional", help="Brain backend (see `flycode brains`)")
    run.add_argument("--control", type=str, choices=["bias", "hard"], default="bias")
    run.add_argument("--llm", type=str, choices=["heuristic", "openai"], default="heuristic")
    run.add_argument("--model", type=str, default="gpt-4o-mini", help="Model name when --llm openai")
    run.add_argument("--max-steps", type=int, default=25)
    run.add_argument("--max-tool-calls", type=int, default=60)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--telemetry-dir", type=str, default=None)

    for name, help_text in (
        ("train", "Connectome-constrained / imitation training (Phase 6-8, §85)"),
        ("distill", "Teacher -> student distillation (Phase 8, §82)"),
        ("benchmark", "Multi-brain benchmark suite (Phase 11, §61-64)"),
        ("lesion", "Virtual lesion ablation study (Phase 11, §67)"),
        ("sensitivity", "Parameter sensitivity sweep (Phase 11, §68)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(_not_implemented=help_text)

    brains = sub.add_parser("brains", help="List available brain backends")
    brains.set_defaults(_list_brains=True)

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
    brain = make_brain(args.brain, seed=args.seed)
    cortex = HeuristicCandidateGenerator() if args.llm == "heuristic" else make_openai_llm(model=args.model)
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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
