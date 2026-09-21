"""Example: running the FlyCode agent loop against a deterministic toy repo.

Usage:
    python examples/quickstart.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flycode import ControlMode, HeuristicCandidateGenerator, SoftwareEnvironment, TaskSpec, make_brain, run_agent


async def main() -> None:
    repo_path = Path(__file__).resolve().parent / "broken_toy_repo"

    task = TaskSpec(
        id="quickstart",
        task="Fix mathutils.add() so both tests pass.",
        fixture="toy_arithmetic_bug",
        max_steps=20,
        seed=42,
    )

    env = SoftwareEnvironment(repo_path, seed=task.seed)
    brain = make_brain("functional", seed=task.seed)
    cortex = HeuristicCandidateGenerator()

    result = await run_agent(env, brain, cortex, task, control_mode=ControlMode.BIAS_CONTROL)

    print(f"success={result.success} steps={result.steps} tool_calls={result.tool_calls}")
    print(f"final_result: {result.episode.final_result}")
    print("\n=== Transcript ===")
    for line in result.transcript:
        print(f"  {line}")


if __name__ == "__main__":
    asyncio.run(main())
