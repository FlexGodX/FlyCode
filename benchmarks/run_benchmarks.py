"""Benchmark runner - multi-brain evaluation on deterministic fixtures (plan §61-64).

Runs every requested brain backend over every fixture task for N seeds with
the heuristic candidate generator (no external LLM needed), collects
RunMetrics per run, aggregates per (brain, task), and writes a JSON report
to benchmarks/results/.

Usage:
    python benchmarks/run_benchmarks.py
    python benchmarks/run_benchmarks.py --brains functional_fly,generic_rnn --seeds 42,127
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator
from flycode.policy import ControlMode

DEFAULT_BRAINS = ["heuristic_fsm", "generic_rnn", "random_reservoir", "functional_fly"]
DEFAULT_FIXTURES = ["toy_arithmetic_bug", "toy_import_bug"]
DEFAULT_SEEDS = [42, 127, 256, 512, 777]

TASK_DESCRIPTIONS = {
    "toy_arithmetic_bug": "Fix the off-by-one bug so the tests pass",
    "toy_import_bug": "Fix the missing import so the tests pass",
}


async def run_one(
    brain_name: str,
    fixture: str,
    seed: int,
    max_steps: int,
    max_tool_calls: int,
) -> dict:
    """Run one (brain, fixture, seed) combination in a fresh temp repo."""
    with tempfile.TemporaryDirectory(prefix="flycode_bench_") as tmp:
        env = SoftwareEnvironment(Path(tmp) / "repo", seed=seed)
        brain = make_brain(brain_name, seed=seed)
        task = TaskSpec(
            id=fixture,
            task=TASK_DESCRIPTIONS.get(fixture, "Fix the bug so the tests pass"),
            fixture=fixture,
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            seed=seed,
        )
        result = await run_agent(
            env,
            brain,
            HeuristicCandidateGenerator(),
            task,
            control_mode=ControlMode.BIAS_CONTROL,
            run_id=f"bench-{brain_name}-{fixture}-{seed}",
        )
    metrics = result.metrics.model_dump()
    metrics["seed"] = seed
    metrics["fixture"] = fixture
    return metrics



def aggregate(runs: list[dict]) -> dict:
    """Aggregate runs into per-(brain, fixture) and per-brain summaries."""

    def summarize(group: list[dict]) -> dict:
        n = len(group)
        return {
            "runs": n,
            "success_rate": sum(r["success"] for r in group) / n,
            "verified_rate": sum(r["verified_completion"] for r in group) / n,
            "mean_steps": sum(r["steps"] for r in group) / n,
            "mean_tool_calls": sum(r["tool_calls"] for r in group) / n,
            "mean_llm_calls": sum(r["llm_calls"] for r in group) / n,
            "mean_wall_time_s": sum(r["wall_time_s"] for r in group) / n,
            "total_regressions": sum(r["regressions"] for r in group),
            "total_recovery_events": sum(r["recovery_events"] for r in group),
            "total_unsafe_proposals": sum(r["unsafe_proposals"] for r in group),
            "mean_rpe": sum(r["mean_rpe"] for r in group) / n,
        }

    by_pair: dict[tuple[str, str], list[dict]] = {}
    by_brain: dict[str, list[dict]] = {}
    for r in runs:
        by_pair.setdefault((r["brain"], r["fixture"]), []).append(r)
        by_brain.setdefault(r["brain"], []).append(r)

    return {
        "per_task": {
            f"{brain}/{fixture}": summarize(group)
            for (brain, fixture), group in sorted(by_pair.items())
        },
        "per_brain": {brain: summarize(group) for brain, group in sorted(by_brain.items())},
    }


def print_summary(summary: dict) -> None:
    header = f"{'brain/task':<45} {'ok%':>5} {'steps':>6} {'tools':>6} {'regr':>5} {'recov':>6}"
    print(header)
    print("-" * len(header))
    for key, s in summary["per_task"].items():
        print(
            f"{key:<45} {s['success_rate'] * 100:>4.0f}% {s['mean_steps']:>6.1f} "
            f"{s['mean_tool_calls']:>6.1f} {s['total_regressions']:>5} {s['total_recovery_events']:>6}"
        )
    print()
    for brain, s in summary["per_brain"].items():
        print(
            f"{brain + ' (overall)':<45} {s['success_rate'] * 100:>4.0f}% {s['mean_steps']:>6.1f} "
            f"{s['mean_tool_calls']:>6.1f} {s['total_regressions']:>5} {s['total_recovery_events']:>6}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brains", type=str, default=",".join(DEFAULT_BRAINS))
    parser.add_argument("--fixtures", type=str, default=",".join(DEFAULT_FIXTURES))
    parser.add_argument("--seeds", type=str, default=",".join(str(s) for s in DEFAULT_SEEDS))
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--max-tool-calls", type=int, default=60)
    parser.add_argument("--out", type=str, default=None, help="Output JSON path (default: benchmarks/results/benchmark_<timestamp>.json)")
    args = parser.parse_args(argv)

    brains = [b.strip() for b in args.brains.split(",") if b.strip()]
    fixtures = [f.strip() for f in args.fixtures.split(",") if f.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    runs: list[dict] = []
    for fixture in fixtures:
        for brain in brains:
            for seed in seeds:
                metrics = asyncio.run(run_one(brain, fixture, seed, args.max_steps, args.max_tool_calls))
                runs.append(metrics)
                print(f"{brain}/{fixture}/seed={seed}: success={metrics['success']} steps={metrics['steps']}")

    summary = aggregate(runs)
    print()
    print_summary(summary)

    out = Path(args.out) if args.out else (
        REPO_ROOT / "benchmarks" / "results"
        / f"benchmark_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {
            "config": {
                "brains": brains,
                "fixtures": fixtures,
                "seeds": seeds,
                "max_steps": args.max_steps,
                "max_tool_calls": args.max_tool_calls,
                "control_mode": ControlMode.BIAS_CONTROL.value,
                "cortex": "heuristic",
            },
            "summary": summary,
            "runs": runs,
        },
        indent=2,
    ))
    print(f"\nResults written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
