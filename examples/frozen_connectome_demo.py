"""Frozen Full Connectome Demo - Phase 5 example.

Demonstrates how the FrozenFullConnectomeBackend can be used as a
production brain backend (benchmark mode).

This is a benchmark substrate, not a production recommendation.
"""

import asyncio
from flycode import run_agent
from flycode.benchmark import BenchmarkRunner
from flycode.brain import make_brain
from flycode.domain import TaskSpec, ControlMode
from flycode.environment import SoftwareEnvironment

# Benchmark runner
runner = BenchmarkRunner(
    brains=["llm", "functional", "frozen_connectome", "generic_rnn"],
    fixture="toy_arithmetic_bug",
    task_template="Fix {fixture_name} so all tests pass",
    seed=0,
    n_replicates=5,
)


async def main() -> None:
    print("Running Frozen Connectome Benchmark\n")
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
