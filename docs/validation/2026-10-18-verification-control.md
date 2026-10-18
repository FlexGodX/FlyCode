# Shared verification control — 2026-10-18

Registered pytest and owner-approved acceptance plans now use the canonical
Store, Coordinator pool and cumulative budgets. Schema21 stores plans,
executions, reservations and ordered events. Draft creation executes nothing;
start/cancel/rerun use revision checks and idempotent commands. CLI exit does not
stop the shared Host. Cancelled, failed and diagnostic attempts retain their
original reports, spending and deadline.

Native execution records process custody before launch, renews its lease and
requires confirmed settlement before releasing capacity. A transient ownership
loss or observed Stop remains terminal for that execution. Changed source,
approval, environment or evidence cannot support current success. Recovery does
not promote an interrupted run to a pass. Event/custody readers reject links and
special files; append operations recheck the owned regular file.

Protected API reads and commands require the local session token, origin and
physical project scope. SSE snapshots pair state with a consistent cursor.
Artifacts are bounded, hash-checked regular files downloaded as attachments;
private manifests and process references are excluded. A prior project grant
allows only cancellation after folder loss. CLI supports trusted workspace,
artifact and integration imports plus preflighted evidence reproduction, while
retaining `verify --repo` compatibility.

The `/verification` screen is linked from classic, Desktop and Tasks. It shows
registered criteria/checks, exact captured version, currentness, budgets, actual
cases, all attempts and safe report previews/downloads. It does not duplicate
the task queue. Existing pipeline results are not automatically imported into
this screen; their common review/integration projection remains aggregate Task8.

## Verified measurements

| Check | Result and source boundary |
|---|---|
| Focused native/API/CLI composition |118 passed,102.04s;53 tracked source/test inputs unchanged during this measurement |
| Final API/CLI metadata and reproduction delta |18 passed,17.94s after adding public criteria/task/workflow fields and trusted CLI origin flags |
| Trusted CLI imports |4 passed,16.11s; actual workspace, artifact baseline/target and integration registration |
| Extracted-wheel API/CLI |18 passed,17.90s; imported package is outside the checkout |
| Final packaged browser |Two plans/six real pytest executions, pass/fail/Stop/rerun/history/download/currentness; zero model calls and agent attempts; owner bytes preserved |
| Compatibility browsers |Existing Desktop, non-Git projects and independent projectless chats passed; model replies in projectless checks are explicit HTTP fixtures |
| Static checks |Ruff53 Python files, scoped mypy24 source files, both JavaScript syntax checks and diff check passed |

Counts overlap and are not summed as unique coverage. The118-case composition
predates the final public metadata/import additions. The later18- and4-case
measurements cover those changes; the final browser covers the subsequent
message-only UI correction. The final53-input boundary remained unchanged.

The final wheel contains169 package files equal to the working source and the
exact UTF-8 README. Its only package difference from the wheel that passed the
18 API/CLI cases is the localized missing-folder message in `verification.js`;
the final browser runs this final wheel after asserting its import location.

Browser checks include actual sleeping-process Stop on390/320, keyboard focus
across polling, root replacement and retained cancellation, stale report response
ordering, lost create response replay, revision conflicts with an explicit second
action, dark Desktop and no horizontal scrolling. The initial UI defects and
the missing readable-error RED remain retained. Folder-picker responses in the
preexisting projects browser are mocked; no new native OS-dialog check is claimed.

Independent review exposed legacy-mode command confusion, a too-small artifact
limit, old-manifest interference, an SSE snapshot/cursor race, stale preview
replacement and lost keyboard focus. Each has a failing reproduction and a
passing correction. Earlier native findings likewise have retained negative
cases. An initial CLI test wrongly expected the parent fixture to own a daemon
launched by a child; the harness now owns its actual launcher handle before CLI
attachment. A mobile Stop raced a real status revision: the browser test now
checks the visible conflict and explicit retry instead of assuming unconditional
success. These are not presented as a clean first attempt.

No full repository suite, real-model run, graph test or new training was run in
this continuation, following the user's feature-only test instruction. Historical
full/native measurements remain historical evidence, not current reruns. Hostile
sandbox and Docker/HTTP/browser runner profiles remain unsupported. This is a
trusted-local POSIX implementation, not isolation from malicious same-user code.

Source boundary SHA256:
`c04d8737a6e5c05abec87b1a2d679a76df2d41bc3a3784892a60b653e0688484`.
Package manifest SHA256:
`47e3b3e9a30e18ccce38c691f26dd4fad8d10e5a0b64a387760905e0ae11bf90`.
Final wheel SHA256:
`477f523a7558373182e766824ab6e2d6b21c363c274dc37974655beabeb4d14a`.
Parent commit:`8886b21909a98b8cd0b1a9397c8ddd32fc1c182f`.
[Machine-readable report](2026-10-18-verification-control.json).

Evidence archive:`~/.cache/flycode/validation/flycode-verification-control-f2x386l_`;
152 regular files/6307138 bytes, independently rehashed. Manifest SHA256:
`45d529cbfbc1f041e5b1d77cd3ea14f8b74cf7f68b14efc465a5cb9c2f8e2d7f`.
Earlier native checkpoints retain their original failure evidence separately.
All measurement handles are terminal.

This delivers the supported registered-pytest TST13 control surface; it does not
close aggregate Tasks5–8, the remaining team review/integration interface,
child-wait/DAG execution or neural orchestration admission. The parallel brain
implementation is excluded from this change. Research §§90–99 and the previous
negative trained-Qwen transfer remain open; no controller, biological topology,
default checkpoint or scientific acceptance changes.
