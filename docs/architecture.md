# FlyCode Architecture

## Overview

## High-Level Architecture

```
USER TASK
   │
   ▼
LLM CORTEX
   ├─ semantic reasoning
   └─ candidate actions generation
   │
   ▼
FLY CONTROL SYSTEM
   ├─ Task Compass / Central Complex
   ├─ Mushroom Body / Associative Memory
   ├─ Valence module
   ├─ Novelty signal
   ├─ Reward Prediction Error
   └─ Action Arbiter
   │
   ▼
ACTION RANKING (Bias Control or Hard Control)
   │
   ▼
TOOL EXECUTOR
   │
   ▼
SOFTWARE GYM
   └─ Environment feedback
   │
   ▼
OUTCOME & LEARNING
```
FlyCode is a hybrid autonomous coding agent that combines:

1. **LLM Cortex** - Semantic reasoning and task understanding
2. **Fly Control System** - Neural controller inspired by Drosophila brain
3. **Software Gym** - Deterministic environment for reproducible experiments

## Proposed multi-agent extension

The current runtime executes one active agent loop. The next extension adds a
durable central dispatcher above isolated worker loops. A workflow-level
connectome ranks task/agent candidates; permissions, dependencies, resource
reservation, artifact review and completion remain explicit gates.

This is a specification, not an implemented runtime. See
[Plan §100](Plan.md#100-оркестрация-нескольких-агентов) and the normative
[orchestration specification](specs/connectome-orchestration.md) for proposed
Phases 15–20, contracts, agent communication and acceptance scenarios. Existing
single-agent admission does not authorize multi-agent scheduling.

## Proposed verification module

A shared verification engine will bind checks to an immutable subject snapshot
and a versioned acceptance plan. Isolated runners produce evidence; a deterministic
evaluator produces the verdict. Testing agents may propose cases and diagnose
failures, while completion and admission remain separate policy gates.

The module covers project correctness, orchestration reliability and controlled
connectome experiments as distinct evaluations. Proposed Phases 21–25, runner
boundaries, trust limits, contracts and acceptance scenarios are defined in
[Plan §101](Plan.md#101-модуль-тестирования-и-проверки-результата) and the normative
[testing specification](specs/testing-module.md). This is not yet implemented;
existing `run_tests`, telemetry and research commands are integration points.
