# Feedback benchmark — 2026-10-16

The English-prompt protocol was frozen before measured execution on runtime
`694d42d`. The existing benchmark runner, real software tools, recorded JSONL
episodes, constrained training CLI and independent pytest runner were used.
Reproduction: [benchmark recipe](../../benchmarks/feedback-v1/README.md).
Machine-readable outcomes: [results](../../benchmarks/feedback-v1/results.json).

## Offline repair outcomes

Each row covers five controller seeds, four fixture families and bias/hard
control: forty actual runs. Budgets are 25 steps and 30 tools per run. A strict
completion below requires both native verified completion and an independent
passing run of unchanged original tests with exact original case IDs. It is
fixture-suite evidence, not a durable multi-agent workflow completion verdict.

| Controller | Strict completions | Mean tools, all runs |
|---|---:|---:|
| Heuristic FSM | 20/40 | 17.00 |
| Random reservoir | 1/40 | 22.75 |
| Generic RNN | 0/40 | 17.95 |
| Functional Fly | 20/40 | 17.00 |
| Frozen Full Fly | 14/40 | 18.00 |
| First trained Full Fly | 10/40 | 20.25 |
| Refined Full Fly | 20/40 | 15.00 |

Mean tools across all runs includes failed/budget-exhausted runs and is not a
cost-per-success metric. Random reservoir produced one additional independently
passing artifact at its limit without native verification; it remains excluded
from strict completion. Raw metrics and both outcomes are retained.
Native `repeated_actions` is populated only for habituation-enabled backends;
zero is not proof of no repetition. Derived test/edit and duplicate tool/argument
counts are therefore retained from actual trajectories. Duplicate calls can be
valid after an edit and are not automatically classified as waste.

The refined candidate raises training-family completion from 14/20 to 20/20:
six new completions, no lost completions. Hard control improves from 4/10 to
10/10. Frozen and refined successful toy repairs both take five tools; the lower
overall mean primarily comes from repairing previously exhausted runs. None of
the heuristic/tournament variants solves the twenty held-out search/boundary
conditions. This is no evidence of semantic generalization.

## Feedback and training

The primary dataset contains ten independently passing Functional Fly episodes,
90 actual actions. In-sample tool imitation rises from 44.4% to 88.9% for each
seed, but task completion regresses. The unsuccessful checkpoints and runs remain
in the report. These teacher trajectories contain repeated unchanged test runs;
imitation accuracy alone is insufficient for selecting an effective controller.

After observing the training-family regression, a separately registered
[exploratory refinement](../../benchmarks/feedback-v1/refinement.json) selects
ten independently passing frozen Full Fly bias episodes on the two training
fixtures only, 50 actions. In-sample imitation rises from 80% to 100%. Training
uses the existing nine-point gain/leak grid and ridge readout fit, one checkpoint
per seed. No held-out trajectory or label is used for fitting or selection.
Repeated held-out evaluation of known project fixtures remains exploratory.

The measured graph has 139,255 neurons and 2,700,513 edges, derived from the
verified FlyWire v783 source files. The actual graph fingerprint remains
`7adea43f9f823703c8116a6321a78adfdc9aac9c14c80e04bfa6d991f8c6712f`;
topology hash is `00d1a94bd95f39f6fde20a6890cc680e538270c5620eadc53a89837d683b644a`.
All ten checkpoints preserve input mappings and mode-readout bytes, change 1,024
tool-readout values and store the selected bounded dynamics. The adjacency is
unchanged; this is an engineering rate model over measured connectivity.

## Real local-model transfer

All three controllers use the same English request, seed 0, single Qwen model,
temperature 0, bias control and two held-out fixtures. Each case permits eight
generation requests and twelve tool/step actions, with a 120s request timeout.

| Full Fly variant | Strict completions | Actual model requests | Actual tools |
|---|---:|---:|---:|
| Frozen | 2/2 | 7 | 12 |
| First trained | 1/2 | 11 | 15 |
| Refined | 0/2 | 16 | 18 |

The six-case phase took 585.54s, 34 actual Qwen HTTP generation calls and 45 tools,
below the registered combined limits. Model starts/ends are paired and sequential;
there are no HTTP/provider errors, scorer calls or fallback. Three cases exhaust
their eight-call budgets. Independent tests agree with every live native result.
The refined helper case executes seven unchanged test runs and no edits: a clear
policy failure despite perfect in-sample imitation. Both refined live repairs
fail. Neither trained checkpoint is suitable to replace the frozen controller.

These six executions are a small transfer smoke, not a statistically controlled
LLM comparison. They demonstrate a regression worth preserving, not a neural
architecture advantage. No further candidate is tuned against these held-out
outcomes in this experiment.

## Scope and retained artifacts

All prompts are English. Pristine fixtures first fail nonempty independent tests:
2 arithmetic, 2 import, 4 search/delegation, 6 boundary/regression cases. The helper
test replaces the shared conversion policy and observes delegation, so a numeric
shortcut alone cannot pass. Full raw repositories, trajectories, model events,
evidence and numeric checkpoints are retained outside Git:

`~/.cache/flycode/experiments/2026-10-16-feedback-v1`.

The candidate checkpoint for seed 0 is `refined-checkpoints/0`; loading it remains
explicit. Application defaults are unchanged. The first candidate regresses, and
the refined one also regresses with Qwen; neither receives production,
scientific or orchestration admission.

Limits: four existing synthetic families, controller seeds rather than proof of
deterministic LLM sampling, no pure LLM-only baseline, no trained matched-compute
RNN comparison, and different neural/training costs. The current telemetry does
not persist provider token usage or establish the exact local model-weight
revision. Failure to establish §§90–99 remains explicit. This work does not close
the pending canonical queue/runtime/integration/orchestration delivery stages.
Native wall time excludes backend construction; execution order and model-cache
state are uncontrolled. Controller dimensions, parameter/cost fields and latency
are retained as descriptive measurements, not a causal speed comparison.

Relevant validation: 38 research/training tests and five opt-in real FlyWire
e2e tests passed; Ruff and identity-module mypy passed. Recipe guards reject
duplicate registration and changed protocol/runtime before executing cases.
