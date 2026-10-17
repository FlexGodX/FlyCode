# Canonical worker custody — 2026-10-17

A canonical worker now has an exact durable `worker-custody-v1` reference before
its guardian starts. Stop and recovery validate custody, saved workspace and
current attempt/fence; a persisted PID never authorizes signals. Unknown HTTP or
pytest outcomes keep their spending/reservations even after the local worker is
proven stopped. Missing, replaced or damaged proof remains blocked.

Checked implementation fingerprint:
`def68ed993c08d1e9191bb5475c27431bd2b5e6026c3e2e92a743086c9175f11`.
Patch SHA256: `c5bde889822357123e5fcddfb4bc8e080e371ccee59ed2b71fa004945c28052b`.

## Verification

The initial three real native failures covered missing pre-spawn reference,
premature release while an actual worker was alive with zero calls, and lost
durable stop proof after coordinator death with an actual pytest descendant.
The implementation then passed all176 focused cases in269.14s in isolation.
After transfer, the same exact176 IDs passed in267.33s on main. Independent
comparison verifies all232 frozen Python/test/config files stayed unchanged.
Ruff passes7 owned files; scoped mypy passes52 source files. All15 new native
cases pass from the extracted wheel, which matches all145 Python files and7
static assets; the extracted CLI lists10 brains. README metadata also matches
the publication build. There was no full-suite, real graph or real-model rerun
on this bounded correction; earlier complete evidence belongs to its own source.

Real cases include duplex FD forwarding/EOF, coordinator death before process
GO, actual descendants after GO, damaged/replaced/missing ownership objects,
guardian death, partial thread startup, stderr retention/flood Stop and FD
allocation failure. A blocked real local HTTP response remains reserved after
worker stop, and its late11-token usage is charged once. Three partial-start
fixture databases independently have zero model/tool calls and stopped workers.

## Compatibility and retained failures

`handle.process` is now an explicitly read-only `ActualWorkerProcessView`:
its PID is the actual child, never the separately owned guardian. `poll` and
`returncode` report only a validated terminal receipt. It has no Popen
kill/terminate/wait methods. Existing diagnostic stderr/Stop assertions remain.
The blocking pytest/tool custody signature and default request hashes remain.

Custody GO admits process creation and trusted Python/brain initialization.
Agent execution still waits for its ready RPC reply, after coordinator targets
are released through `launch_ready`. A partial thread-start failure cannot admit
agent model/tool work. The watchdog is a liveness bound, not that admission gate.
There is no universal constructor bound for arbitrary substituted worker argv.

All failed runs remain. Three old parent-Popen fault injectors needed adaptation
at the hash-bound launch boundary. An unretained temporary socket in two fixture
scripts caused a real EOF cancellation after51200 stderr bytes in the first
composition (175 pass/1 fail); only fixture socket lifetime was corrected, with
all output thresholds, deadlines and Stop assertions unchanged. A test field
lookup and a descriptor-allocation leak were separately diagnosed; the latter
has a real failing-then-passing native case. The first seal manifest incorrectly
hashed its own still-running output; it was rejected and retained. Manifest v2
was independently checked after generator exit; production/test bytes and176
case results were unchanged. Setup/test-adaptation failures are not feature REDs.

## Boundaries and next work

Old worker rows without exact ProcessRef cannot be upgraded from PID. A free
lock plus an admitted/startedFalse receipt cannot prove no fork occurred;
guardian death in that gap stays uncertain. A surviving guardian can establish
an actual never-started receipt. Stop/reconciliation does not require fresh
physical source registration, while new execution/source effects still do.

This is trusted local process-group custody, not a hostile-code sandbox or a
proof about escaped/remote effects. It does not add unique database host
leadership, an execution/verification HTTP host, or scientific admission.
Aggregate Tasks5–8 remain open. A separate Tasks UI is still isolated at this
publication and is not included in its source or measurements.

Evidence root: `/private/tmp/flycode-worker-main-validation-c62q948q`.
The JSON companion records checked hashes, counts and retained delivery metadata.

Sealed archive: `~/.cache/flycode/validation/flycode-worker-custody-c62q948q`,
7647 regular files /145308812 bytes, independently checksum-verified. Archive
copies are historical evidence, not authority to reconcile live processes.
