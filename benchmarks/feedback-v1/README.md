# FlyCode feedback benchmark v1

The registered protocol is `protocol.json`. All task prompts are English.
It measures actual repairs through existing FlyCode tools, not descriptions of
expected outcomes. `run.py` adapts the existing benchmark, telemetry, training CLI
and independent pytest verification; it does not replace their implementation.

From the repository root, with a prepared verified FlyWire graph:

```bash
export PYTHONPATH=src
RESULTS="$HOME/.cache/flycode/experiments/my-feedback-run"
.venv/bin/python benchmarks/feedback-v1/run.py init "$RESULTS"
.venv/bin/python benchmarks/feedback-v1/run.py baseline "$RESULTS"
.venv/bin/python benchmarks/feedback-v1/run.py teacher "$RESULTS"
.venv/bin/python benchmarks/feedback-v1/run.py train "$RESULTS"
.venv/bin/python benchmarks/feedback-v1/run.py trained "$RESULTS"
# Optional, explicitly exploratory response to the failed first training candidate:
.venv/bin/python benchmarks/feedback-v1/run.py refine "$RESULTS"
# Requires the specified model already available in the local LM Studio endpoint:
.venv/bin/python benchmarks/feedback-v1/run.py live "$RESULTS"
.venv/bin/python benchmarks/feedback-v1/run.py audit "$RESULTS"
# Complete protocol including refinement; writes the external summary.json:
.venv/bin/python benchmarks/feedback-v1/analyze.py "$RESULTS"
```

Use a new output directory. The registration binds source identity, protocol,
environment and biological graph. Subsequent stages reject a changed runtime or
protocol. Nothing changes the application's default checkpoint. Five checkpoints
are saved separately under `checkpoints/<seed>`; loading one remains an explicit
choice using `--brain full --checkpoint ... --seed ...`.

The baseline is 200 cases: five controllers × five seeds × four fixture families
× two controls. Training uses ten successful arithmetic/import teacher episodes
only, independently checked against unchanged original tests. A failed teacher
audit blocks training. Each of five independently initialized Full Fly controllers
is trained on the same curated dataset. The trained evaluation adds 40 cases.
The LLM phase adds four cases on the two held-out fixtures, seed 0, bias control,
with 32 model calls and 48 tool calls maximum across that phase. Its final
120 seconds are reserved for an outstanding request to finish; after the earlier
deadline the cortex stops requesting inference. Requests are sequential.

`refinement.json` records a follow-up chosen after observing the first candidate's
training-family regression. `refine` curates the ten independently passing Full
Fly bias episodes from the frozen baseline, trains five separate checkpoints and
runs forty additional cases. It never selects using held-out outcomes. If this
stage ran, `live` also includes the refined checkpoint: six live cases, at most
48 model calls and 72 tools under the same phase deadline. Repeated evaluation
of existing held-out fixtures is exploratory; it is not a fresh confirmatory set.

`audit` reruns pytest using the registered bounded runner. It checks original test
file hashes and exact collected case IDs against pristine negative controls;
nonempty passing cases are mandatory. Feedback records actual independent
results alongside native run metrics, including failures. This is a registered
fixture-suite measurement, not a multi-agent workflow completion verdict.

Keep raw repositories, events, telemetry, evidence and numeric checkpoints in the
external output directory. The small public results can include hashes, configs,
metrics and audit outcomes. Never commit biological data or private model output.

This benchmark is deliberately limited: four existing synthetic fixtures, an
explicit controller in every variant, no pure LLM baseline or matched neural
compute, and one live-model seed. Equal controller seeds do not make local model
sampling deterministic. Held-out fixtures were excluded from this run's training;
they have been seen during earlier project development. Report imitation accuracy,
task success, costs and transfer separately. Do not infer scientific acceptance,
orchestration admission or production promotion from this experiment.

The published [measured results](results.json) retain all 296 agent runs:
280 offline evaluations, ten teachers and six live-model evaluations. Refined
training raises toy-family completion from 14/20 to 20/20, but Qwen transfer
regresses from 2/2 to 0/2. Neither trained candidate is activated. See the
[validation report](../../docs/validation/2026-10-16-feedback-benchmark.md) for
all baselines, negative results, feedback and limits.
