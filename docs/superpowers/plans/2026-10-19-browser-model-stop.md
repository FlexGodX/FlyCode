# Browser Stop during an actual model request

**Goal:** A protected browser Stop remains responsive while the real coordinator
owns an unfinished model HTTP request. The owned worker stops first; model
spending, remaining reservation and current assignment stay pinned until the
actual late response is accounted. Its edit candidate never reaches a tool or
the source folder.

**Scope:** Plan §§100/103/104, delivery Tasks5/8. Acceptance-only extension of the
existing native coordinator browser harness. The existing internal held-HTTP
test is evidence for existing behavior, not automatic browser coverage.

**Approach:** Extend coordinator_runner.py/coordinator.cjs with a model stage,
using the existing approved pipeline, real English HTTP completion, owned
application/control channels and server-owned worker receipt. A bounded32-read
burst overlaps Stop; six exact duplicate Stop requests must return its original
receipt without new commands or work. Do not inject a worker, dispatch tick,
process signal or synthetic result. No production hook or neural edit.

The attachment socket admits eight concurrent peers. The initial all200 browser
assumption failed on409 attachment_identity, which conflates closed/failed
transport and identity rejection; it does not prove an identity change or Stop
failure. Retain all32 outcomes: require every200 to match exact session, allow
only this fail-closed409, then require authenticated same-session200 after drain
without a new attach/probe. Collect every outcome without unhandled rejection.

Count command idempotency by the web actor/key and target task revision. The
second native run confirmed Stop68ms/physical custody before HTTP and six exact
receipts, then failed an overbroad total-command-count assertion: the daemon may
legitimately append its own empty host-selection audit. Preserve that failed run;
no duplicate Stop record, extra attempt, inference or tool effect is permitted.

1. [x] Pin exact published478bc84 source and read helper/returned model signatures.
2. [x] Add a real HTTP Event gate after catalogue admission; activate it only for
   the actual workflow model request. Keep cleanup release owned before daemon
   teardown. Record request/attempt/budget/deadline before Stop.
3. [x] Browser attaches explicitly, closes the first application, clicks Stop in
   the second application under the read burst and replays the exact request.
   Require response<1s, no page errors, real current worker-group stop while
   HTTP is still running, unchanged expenditure/reservation and no new work.
4. [x] Release actual HTTP; require late accounted usage, retained deadline,
   no edit/tool effect and preserved dirty/untracked bytes. Check pause/cancel
   both with and without root loss; retain every failed preparation/measurement.
5. [ ] Run feature browser cases against exact wheel and frozen merged source,
   changed-file static checks/UTF8 README/CLI, and1440/390/320 screenshots. Archive
   evidence, update Plan/handoff/README RU/EN and publish only checked own changes.

Automatic message/request avalanche is a separate open prerequisite: the public
dispatcher does not pass owner-approved communication routes to live workers.
This HTTP read/Stop replay burst does not establish automatic agent messaging,
task creation from message content, biological benefit or full delivery.

Checked [evidence](../../validation/2026-10-19-browser-model-stop.md); publication is recorded separately.
