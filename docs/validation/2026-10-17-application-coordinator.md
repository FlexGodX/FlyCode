# Detached application coordinator and historical Stop — 2026-10-17

Validated bounded continuation of `a30b6e4acab5f2bae56479c7569a35aa3e38a4e0`. Aggregate Tasks5–8
and scientific §§90–99 remain open.

An explicit trusted launcher attaches to the incumbent or starts the existing
coordinator in a detached session with DEVNULL stdin and an owned regular output
file. It establishes its reaper before spawn. Only the child Host elects or
recovers ownership; failed attachment never signals a discovered PID or repairs
metadata. Closing the launching application leaves the coordinator running.

Protected `/api/tasks/coordinator` and `/api/tasks/coordinator/attach` expose only
four status fields. The POST accepts an empty strict body and cannot select paths,
roles or scope. Existing token/origin protections remain in force. Attachment
creates no task, approval, model probe or model load.

Schema19 records the first immutable actor/project Stop grant after successful
physical resolution. It does not backfill legacy IDs or infer grants from creator
metadata. `/api/tasks/{id}/stop` accepts only pause/cancel, expected revision and
idempotency key. After root loss or tombstoning it can stop old work while reads,
resume and fresh execution remain denied. Its four-field receipt and revision
conflicts never disclose a WorkItem. A command receipt does not assert process
absence: actual stopped custody is checked separately; unknown effects retain
reservations. Grant/task project IDs are immutable under the current APIs.

## Verification

| Check | Result |
|---|---|
| Final default collection | 1158 exact IDs: 1150 pass, 8 expected opt-in skips, 0 failures/errors; 1091.355s |
| Frozen implementation | 255 source/test/static/config files, plus both README hashes |
| Composed native/API/migration selection | 185 pass,157.49s; repeated64/7/1 runs are not extra unique coverage |
| Extracted wheel | 133 exact IDs,266.963s; 159 exact package files/README; CLI10 pass |
| Real FlyWire smoke | 5 pass,0 skips; no scientific admission |
| Static checks | Ruff pass; scoped mypy4 pass |
| Actual English Qwen pipeline | 84.158s;3model/34tool calls; writer/review/integration and current result pass |
| Independent provider receipts | 4 matched requests:1 explicit probe+3workflow; model, timestamps and token usage agree |

The native tests run two actual application processes and a detached coordinator.
The first application exits before the second pauses real review/integration
subprocesses, with both present and replaced source roots. Each HTTP response is
less than1s by the test; exact stopped receipts, spending/deadline, unknown
effect holds and owner dirty/untracked bytes are asserted. Root-loss/restart,
unknown actor/task privacy, replay, stale revision, two-connection grants and
nonempty schema18 migration rollback/no-backfill are covered. Owned SIGKILL/restart
rejects prior-session authentication. A held live Host without IPC is not fenced.

Retained feature REDs: absent launcher/API5, missing grants/Stop/migration4 and
reaper-start failure1. The initial native observation failure waited on an exclusive
custody lock too early; the final test waits for the actual stopped receipt with
the same expense/source assertions. The denied-socket run is environment evidence,
not a feature RED. The first wheel setup lacked hatchling; isolated pip build then
succeeded. No failure has been discarded or converted to a pass.

Real model request: “Set the answer variable in src/example.py to exactly 1. Preserve all tests, README and user files. Run the tests to verify your change.” It uses an explicitly selected
functional controller and already loaded Qwen; no trained checkpoint, fallback,
automatic retry or scientific promotion. Owner bytes are preserved, all three
attempts succeed with stop confirmation, and reservations/uncertainty are zero.
Two pre-repair run_tests failures remain in the writer ledger and episode: each
reports tests/test_example.py:3, assert0==1/1failed. Their structured error_class
is absent; no named error code is inferred. After the edit an independent run
reports1passed. The live audit records their exact persisted output and independently
checks371 evidence hashes and29 exact stopped process receipts. This one-task smoke does not establish benchmark efficiency.
The earlier [296-run feedback benchmark](2026-10-16-feedback-benchmark.md) retains
negative Qwen transfer and its rejected trained candidates separately.

## Evidence and limits

Source manifest: `2125e78446b4576161b05cb6be5648e9258155e46f9f6dbf58ad835dc2de2987`. Runtime fingerprint:
`4ed081a59d196173841ad39301a398252dc753492a2d6fe25d8ebfe0225b38f3`. Wheel SHA256: `85b7bf293172784fe50526c0f806296e27a4a434fc080c59b796f3e447c7821b`.
Full plan/registered IDs, XML/logs, frozen hashes, actual-model SQLite/artifacts,
provider window and audit reports are retained under `/private/tmp/flycode-launch-stop-validation-96xzxksa`. The regular-file
archive is `/Users/Administrator/.cache/flycode/validation/flycode-application-coordinator-96xzxksa`;
its verified manifest and exact public commit are recorded in the local publication
receipt after sealing. Closed focused/live/final audit reports are retained with
the archive; they distinguish raw runtime evidence from asserted transient test
results. Transient per-test native SQLite/custody folders were not independently
archived by the focused audit.

This is a trusted local same-user boundary. No hostile sandbox, UI attachment
button, CLI attachment command, general execution/verification interface or neural
orchestration admission is claimed. Static UI bytes are unchanged and no browser
rerun is relabelled as current acceptance. Continue the required remaining work.
