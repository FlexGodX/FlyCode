# FlyCode Quick Start

## Installation

```bash
pip install -e ".[dev]"
```

## Basic Usage

### Running with Functional Fly

```bash
flycode run \
  --repo ./my-project \
  --task "Fix the FastAPI application and get tests passing" \
  --brain functional \
  --control bias
```

### Available Brains

```bash
# Mock brain (for testing)
flycode run --repo ./project --brain mock

# Random reservoir (baseline)
flycode run --repo ./project --brain random

# Generic RNN (baseline)
flycode run --repo ./project --brain rnn

# Functional fly (production)
flycode run --repo ./project --brain functional

# Full connectome (research only)
flycode run --repo ./project --brain full
```

### Control Modes

```bash
# Bias control (default, LLM leads, fly influences)
flycode run --brain functional --control bias

# Hard control (fly decides, research only)
flycode run --brain functional --control hard
```

## Python API

```python
from flycode import SoftwareEnvironment, TaskSpec, FunctionalFlyBackend

# Create task specification
task = TaskSpec(
    task="Fix the FastAPI app and make tests pass",
    repo_path="./my-project",
    expected_steps=10,
    seed=42,
)

# Create environment
env = SoftwareEnvironment(
    repo_path="./my-project",
)

# Reset environment
obs = env.reset(task=task)

# Run episode (agent loop)
transitions, final_obs = env.run_episode(task=task, max_steps=20)

print(f"Completed in {final_obs.step_number} steps")
print(f"Tests: {final_obs.tests}")
```

## Programmatic API

```python
from flycode.brain import FunctionalFlyBackend

# Create brain
brain = FunctionalFlyBackend(
    task_compass_dim=4,
    mb_odes=50,
    mb_sparsity=0.05,
)

# Reset
brain.reset()

# Process observation
from flycode.brain import NeuralStimulus
stimulus = NeuralStimulus(
    observation=observation_dict,
    target_behavior="EXECUTE",
)
state = brain.step(stimulus)
```

## Behavior Modes

```python
from flycode.policy import BehaviorMode

# Set behavior mode
mode = BehaviorMode.EXPLORE  # or DIAGNOSE, PLAN, EXECUTE, etc.

# The controller will adapt based on this
```

## Safety

```python
from flycode.safety import SafetyRiskPolicy

# Create policy
safety = SafetyRiskPolicy()

# Check action
is_safe, risk, reason = safety.check_action(action)

if not is_safe:
    print(f"Action blocked: {reason}")
```

## Next Steps

See `docs/architecture.md` for detailed architecture.

See `examples/` for more examples.