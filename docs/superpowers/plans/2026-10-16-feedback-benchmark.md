# Feedback Benchmark Implementation Plan

**Goal:** Run a reproducible English-prompt benchmark, curate observed training feedback,
train a separate biological controller checkpoint, and measure held-out transfer.

**Architecture:** Reuse `research.benchmark`, JSONL telemetry, `load_episodes` and
`train_connectome`. Keep measured biological adjacency and original fixture tests
immutable. Training fixtures are arithmetic/import; search and boundary cases
are held out of this training run (existing project fixtures, not unseen research data).

**Tech Stack:** Existing Python/Pydantic/numpy/scipy/pytest CLI; local LM Studio.

**Spec:** `docs/Plan.md` §§53–64, 79–80, 90–99; user benchmark/training request.

## Steps and checks

- [x] Reproduce fingerprint omission with failing training/telemetry mutation tests;
  include both packages, then pass identity/benchmark/constrained-training tests.
- [ ] Freeze `benchmarks/feedback-v1/protocol.json` before any measured run.
  Validate all generated `BenchmarkConfig` inputs. Verify source file checksums
  and graph fingerprint against the released-source metadata.
- [ ] Run five frozen baselines (FSM, reservoir, RNN, Functional Fly, Full Fly),
  five seeds, four fixtures, bias/hard: 200 cases. Record every failure and cost.
- [ ] Record ten Functional Fly heuristic teacher episodes, independently rerun
  unchanged original tests; curate successful training fixtures only. Train one
  checkpoint per seed using the existing CLI. Check immutable topology and report
  in-sample accuracy separately from task success.
- [ ] Run trained Full Fly on the same forty conditions. Independently verify all
  measured repositories against the original test bytes and retain raw evidence.
  Compare paired outcomes/tool calls, including train/held-out splits separately.
- [ ] Run frozen/trained Full Fly with the loaded local Qwen model on two held-out
  fixtures, seed 0, bias, single-model mode. Limit each case to 8 model calls and
  12 tools/steps; entire live phase to 30 minutes, one sequential inference stream.
  Retain HTTP model events/errors; no fallback or automatic model loading.
- [ ] Publish protocol, compact raw results, feedback, checkpoint manifests/hashes,
  reproducible instructions and honest limitations in README RU/EN and handoff.
  Run relevant checks and commit/push. Keep the wider delivery goal active.

## Acceptance and limits

All task prompts must be English. Retain unsuccessful and timed-out cases in the
denominator. Tests must match the pristine fixture, pass nonempty collection and
remain outside the training dataset. Compare identical per-case budgets/seeds;
report actual compute differences, never claim matched neural/training compute.
Report mean and per-seed success/tool costs. Training and evaluation on the same
two toy tasks establish imitation only. Held-out means excluded from this run's
training, not unknown to the repository or previous research.

A candidate improves engineering performance only if held-out success rises, or
success is unchanged and mean tools falls by at least 10%, with no new regressions
or unsafe execution. Do not activate a regressing candidate or rewrite failures
after observing results. This small fixture benchmark cannot establish §§90–99
scientific acceptance or orchestration admission; no such promotion is requested.
