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