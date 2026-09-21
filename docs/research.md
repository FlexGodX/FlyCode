# FlyCode Research Methodology

## Introduction

FlyCode investigates whether fly-inspired neural architectures can provide measurable advantages for autonomous software engineering.

## Research Principles

### 1. Hypothesis-Driven

**Primary Research Question:**
> Can computationally-dense recurrent dynamics from real connectome and functional fly circuits give autonomous coding agents measurable advantage in robustness, adaptation, tool selection, and long-horizon strategy maintenance?

**NOT trying to prove:**
- "Fly brain can program code"
- "More neurons = better"

**Valid results include:**
- Functional Fly > Generic RNN on matched-compute benchmark
- Specific fly modules show specific advantages
- Even Generic RNN > Functional Fly (null result is still valuable!)

### 2. Matched-Compute Rule

All comparisons must control for:
- Parameter count
- Hidden state size  
- Latency
- Training examples
- Training budget

### 3. Reproducibility Requirements

Strong result requires:
- Multiple tasks (10+ diverse tasks)
- Multiple seeds (5+ seeds per brain)
- Matched compute budget
- Stable effect across runs
- Specific ablation results
- Robustness to parameter variations

### 4. Causal Analysis

Use selective lesions to understand what works why:

| Lesioned Module | Expected Deficit |
|----------------|-----------------|
| Central Complex (CX) | Long-horizon strategy persistence |
| Mushroom Body (MB) | Fast adaptation, reversal learning |
| Valence | Risk/reward arbitration |
| Novelty | Exploration |

## Training Pipeline

### Phase 1: Imitation Learning

```
Teacher (high-quality LLM agent or human-approved)
    ↓
Trajectories (state → action → outcome)
    ↓
Fly core learns: state → good behavior bias
```

**Targets:**
- Behavior mode selection
- Action preferences
- Recovery from failures
- Delayed reward handling

### Phase 2: Environment Optimization

After imitation, optimize on deterministic benchmarks:

```
SoftwareGym environments (10+ task types)
    ↓
Task-specific optimization
    ↓
Improved policy
```

**DO NOT do online RL on live repository.**

### Phase 3: Online Learning

Production controller with bounded updates:

**Allowed:**
- Mushroom Body associative weights
- Readout weights
- Small modulatory biases

**Forbidden:**
- Automatic large-scale policy changes
- Drift without limits

### Phase 4: Safety Limits

Add safeguards against drift:
- Max update norm
- Learning rate cap
- Replay rollback
- Policy snapshot
- Drift detector

## Benchmark Categories

### Simple Tasks
- Single bug fix
- Minor refactor

### Complex Tasks
- Multi-file bug
- Docker configuration
- Database setup
- End-to-end workflow

### Stress Tests
- Partial observability
- Delayed reward (10-20 steps)
- Context reversal (previous strategy fails)
- Noisy feedback (stochastic tests)
- Novel repository (unknown framework)
- Resource constraints (max tool calls)

## Research Server

Separate research service for full connectome:

**API:**
- POST /episode
- POST /stimulate
- POST /step
- POST /lesion
- GET /state

**Features:**
- Virtual stimulation
- Virtual lesions
- Neural tracing
- Parameter sweeps
- Trajectory generation

## Metrics

### Task Performance
- Success rate
- Verified completion
- Steps to completion
- Tool calls
- LLM calls
- Tokens used

### Neural Signals
- Neural state variance
- RPE statistics
- Policy entropy
- Strategy switches

### Quality
- Task quality score
- Utility (completed goal)
- Aversion (harm done)
- Regressions introduced

### Efficiency
- Wall-clock time
- Compute cost
- Latency

## Success Criteria

### Research Success
Project is successful if we find:
- Measurable advantage of fly-derived mechanisms
- Understanding why specific mechanisms work
- Clear boundaries of effectiveness
- Reproducible results

### Engineering Success  
Fly control provides:
- Better task quality OR
- Fewer tool calls OR
- Faster adaptation OR
- Better safety

**Without unacceptable:**
- Latency increase
- Compute cost
- Policy instability

## Key Deliverables

1. **SoftwareGym** - Deterministic environment
2. **Brain Backend API** - Interface for brain implementations
3. **Baseline Controllers** - FSM, RNN, Reservoir
4. **Functional Fly Implementation** - Production controller
5. **Benchmarks** - Comprehensive test suite
6. **Results** - Quantitative comparison
7. **Lessons** - Scientific findings

## Ethics

- All experiments run in isolated environments
- No live production repositories
- Clear separation research vs production
- Transparent about limitations

---

**Bottom line:** We're discovering what works, not forcing a narrative.