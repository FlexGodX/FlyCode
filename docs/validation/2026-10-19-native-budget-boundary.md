# Native budget boundary and dispatch contention

The approved 64-task hierarchy now carries question/answer, two rejected writer
attempts and one owner graph revision through the common automatic Coordinator.
Dispatch no longer repeats the full authority traversal for every task/instance
pair while holding the SQLite writer lock. The final review exhausts its tool
reservation before a third check can start; the workflow correctly stays incomplete.

| Immutable run | Seconds | Maximum observed writer hold, seconds | Workflow HTTP + probe | Tools | Stopped groups |
|---|---:|---:|---:|---:|---:|
| source | 149.82 | 0.614 | 48 + 1 | 120 | 83 |
| extracted | 136.88 | 0.695 | 48 + 1 | 120 | 83 |
| frozen | 191.88 | 0.886 | 48 + 1 | 120 | 83 |

These are bounded software acceptance runs with a controlled local HTTP provider,
real SDK requests, separate native workers and registered pytest subprocesses.
Every run preserves source/test fingerprints, the original source/dirty bytes,
the fixed 1800-second deadline, zero final reservations and no uncertainty.
There is one active workflow; the old fixture workflow stays an unapproved draft.
The public wheel contains 189 exact runtime files. The frozen composition has
246 files and includes parallel controller changes without publishing them.
Its workers use heuristic_fsm; this is not configured-neural or biological admission.

## Actual boundary and retained outcomes

- 28 writer/reviewer pairs, seven structural groups and one integrator make 64 tasks.
  No new task is created by retry, question or replan. The separate capacity
  regression also refuses task65 and passes in29.93s on the extracted wheel.
- Four actual native workers hold slots; a fifth issued request stays queued until
  confirmed exit. Two owner replans while workers are live refuse stop_unconfirmed.
- B sends a real question; A answers with its causation ID. B's actual request
  consumes the answer and its inbox records received. The controlled provider
  retains this parsed value until B's final edit; this does not measure stateless
  model memory or problem-solving quality. Both route edges are disabled before
  replan by replacing their grant with an empty grant.
- A publishes0→-1, then the bounded retry inherits-1 and publishes-2. Both independent
  negative reviews and exact artifact bytes remain. One owner replan plus exact
  replay admits A's third attempt from original0; the old pipeline and settled
  reservation rows remain unchanged. A–E receive five positive reviews.
- F asks for four models/three tools but receives only the remaining three developer
  calls. Total developer HTTP is48; twelve of the60 model allowance remain protected.
  Ordinary reviewers execute registered checks without inference. This run therefore
  does not claim60 native-worker HTTP calls; earlier Host-service saturation is separate.
- The eight reviews produce two changes_requested, five approved and one inconclusive.
  Seventy-two earlier registered checks plus46 ordinary tools leave two tools.
  F's review reserves exactly two and runs two baseline pytest invocations. The third
  has state cancelled, error_class policy, reason budget_exhausted, empty argv and
  no process. Subsequent checks cannot yield pass. F has no completion, the21 remaining
  writers stay planned, and no integration/source application occurs.
- The120 tool outcomes are112 completed and8 failed ordinary run_tests gates.
  The failed gates retain the original wrong outputs; no tool outcome is uncertain
  or acknowledged late. These eight failures are separate from registered checks.
- Seventeen attempts include nine actual native workers and eight review services;
  nine worker groups plus74 registered pytest groups are independently confirmed
  stopped. All48 paid HTTP callbacks admit an independent SQLite writer. Endpoint
  intervals do not overlap. A strict three-second window admits no new effects.

## Reproduced failure and minimal fix

The first harness wrongly assumed the scheduler would choose expensive A/B before
cheaper tasks. Owner-approved priority3 now orders A–D; defaults and scheduler
weights stay unchanged. That52.21s failure remains. A second428.45s run lost A3's
terminal result and recorded BrokenPipeError:worker_channel; its original cause
was not retained and is not retroactively asserted. All68 recorded groups stopped.

The third run exposed Coordinator.expire failing with database is locked. The
harness had waited for an unreachable budget count after its background future
failed; an owned-process interrupt at383.13s revealed the stack. Its67 recorded
process groups were absent. The fourth diagnostic run failed at166.91s and measured
an admission dispatch_bound writer transaction lasting5.72765s, beyond SQLite's
five-second busy wait. Its67 groups were also confirmed absent.

The dispatch loop was calling effective/receipts/full guard_metadata for every
candidate task and instance. A real64-task regression counted197 complete graph
checks inside one writer transaction, instead of one. The minimal Store change
reuses the already checked recipe/receipts only inside that same transaction,
before assignment; all per-pair permissions, roles, dependencies, budgets, capacity
and reviewer independence still apply. Standalone claim/claim_next retain their
own full guard. No global timeout or authority rule was relaxed. Semantic and
binding tamper cases remain blocked. The native harness now propagates failed
Coordinator futures immediately and retains bounded RPC/transaction diagnostics.

The initial unit fixture incorrectly loaded8192 while declaring16384 and failed
before testing dispatch. It was corrected before runtime/profile creation; this
is setup evidence, not the feature RED. The actual RED was1 failed/2 passed in7.78s
with196 extra guards. GREEN is3 passed in6.38s. Another88 related queue/admission
checks pass45.01s. The extracted and frozen packages each pass the three new cases
in6.26s and6.89s. Ruff and Store mypy pass; no full suite or UI rerun was used here.

## Reproduce in the development environment

```bash
.venv/bin/python -m pytest tests/unit/orchestration/test_hierarchy_dispatch_guard.py -q
PYTHONPATH=src .venv/bin/python tests/browser/saturation_combined_runner.py --prepare-only --output "$(mktemp -d)/preflight"
PYTHONPATH=src .venv/bin/python tests/browser/saturation_combined_runner.py --output "$(mktemp -d)/run"
```

All model requests/objectives/messages are English. Evidence is external to subject
and runtime source. Package byte parity, UTF-8 README metadata and extracted CLI
help were checked. Regular-file archives retain failures, source hashes, requests,
SQLite ledgers, manifests and actual process custody; special files are never hashed.

The overall specification goal stays active. Scientific promotion/admission,
real model/graph measurements and final aggregate/full-suite gates remain open.
A safe incomplete boundary does not establish verified workflow completion.
