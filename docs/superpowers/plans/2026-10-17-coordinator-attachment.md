# Authenticated coordinator attachment implementation plan

**Goal:** Applications attach to one physical database coordinator without owning its lifetime or granting execution permission.

**Architecture:** A private per-session Unix socket authenticates bounded read-only attach requests. The existing Host retains kernel leadership, physical database registration and recovery. An explicit trusted launcher starts a detached coordinator only; canonical task commands and approvals remain in the existing Store.

**Tech Stack:** Python stdlib sockets/HMAC/threading/subprocess, Pydantic, SQLite, native pytest.

**Spec:** `docs/Plan.md` §§100–104, `docs/specs/connectome-orchestration.md` AC-07/08/12, and Task2 of `2026-10-17-managed-coordinator.md`.

## Global constraints

- One active workflow; max4instances/64tasks/3attempts/60model calls/120tools/30min; verification reserve20%.
- No SQLite transaction spans socket I/O, filesystem inspection or inference.
- Socket existence, diagnostic timestamps and PIDs never grant Host leadership.
- Client close detaches only the client. Unknown effects and historical source custody remain held.
- No automatic probes/model loading/fallback, new approvals or biological topology changes.
- Trusted local same-user boundary; private credentials do not isolate hostile processes of the same OS user.

## Review focus

1. Competing clients and pathname aliases must authenticate the same session without either client recovering it.
2. A live kernel lock with absent IPC remains busy; no client fences, unlinks or signals the owner.
3. Slow/malformed peers must not delay another attach, coordinator heartbeat or Stop.
4. Replaced descriptor/socket/parent or stale generation fails closed; cleanup removes only exact owned objects.
5. Native socket paths must fit even when the database/runtime hierarchy is long.

### Task1: Private bounded endpoint and read-only client

**Files:** create `src/flycode/orchestration/attach.py`, `tests/unit/orchestration/test_coordinator_attachment.py`; modify `coordinator.py` only for endpoint lifetime and `wire.py` only for optional frame/deadline limits.

**Interfaces:** `AttachmentServer(host)` owns endpoint publication/handlers, not the Host. `CoordinatorClient(database).status()` returns a strict sanitized `AttachmentStatus` for the current registration/session/generation. `close()` detaches. Descriptor discovery uses read-only SQLite and immutable physical pins; it never opens a writable Store or enters Host.

- [x] Native RED: start the current coordinator, observe actual ready session, require its runtime to publish a private endpoint. The missing endpoint must fail before implementation.
- [x] Minimal endpoint: protected 0600 descriptor, per-session nonce, short 0700 socket parent, exact socket/parent identity, closed request/reply schemas and domain-separated HMAC with fresh echoed challenge.
- [x] Real socket tests reject wrong nonce/generation, extras, oversized/truncated/slow input, stale/fake replies and substituted files without queue changes. Test actual native long path and private modes.
- [x] Two independent read-only clients attach to one owner; closing one preserves generation/heartbeat and the other's status. No implicit tasks or inference.
- [x] Native crash/restart rejects old session and preserves Host authority; endpoint cleanup does not remove replacements.

### Task2: Trusted detached launch and protected application attachment

**Files:** `attach.py`, `web/tasks.py`, `web/app.py`; native attachment tests and `tests/unit/web/test_canonical_tasks_api.py`.

**Interfaces:** `attach_coordinator(database=default_state_path())` first authenticates an incumbent and otherwise launches the existing coordinator module with detached session, stdin DEVNULL and owned log output. Browser attach accepts an empty strict body under token/origin-protected `/api/tasks/coordinator/attach`; it cannot select DB/root/scope. Client close never stops the Host.

- [x] Real racing launcher RED; one actual Host session, detached child survives first app exit, exact test-owned Popen handles clean up test processes only.
- [x] Held live lock/absent IPC refuses takeover; bounded startup wait reports failure without PID signals or changed queue records.
- [x] Protected API RED for absent token, foreign origin and extra path/scope fields; successful responses contain no credentials or raw filesystem paths.
- [x] Actual approved pipeline survives first app close; another app's protected pause/cancel remains responsive during held review/integration. Preserve spending/deadline, late usage, exact stopped receipts and dirty/untracked bytes.
- [x] Missing-root Stop remains subject to durable prior grants; do not widen fresh project resolution for convenience.

### Task3: Acceptance and publication

- [x] Independently inspect retained RED/green logs and immutable source/evidence; run relevant native/API tests, Ruff/mypy and final default suite without concurrent source edits.
- [x] Wheel/extracted CLI and explicit real-model/graph smoke; preserve opt-in skips separately. This API-only increment changes no static UI bytes; actual attachment controls/browser acceptance remain open.
- [ ] Update Plan/handoff/README RU/EN, seal regular-file evidence, commit and push verified changes. Aggregate Tasks5–8 and neural/scientific criteria remain open until their full acceptance.

Parent core publication: `cf629609423130e5acbc5ba7fb4c972cb6e3aabd`. Its 1130-case validation proves the parent, not future attachment bytes.

Current bounded endpoint evidence is accepted: final12 native cases pass2.97s;
full1142 exact IDs pass1134/8same expectedskip,0fail/error,252 unchanged files,
1085.932s. Wheel120/159 exact package bytes/README and CLI10, real graph5,
Ruff/mypy3 and actual English Qwen pipeline85.104s/3model/34tools pass. Four actual
provider HTTP receipts match SQL; no retry/fallback/promotion. Independent audit
passes3561 checks with698 stable input hashes. Initial endpoint/atime and startup
cleanup REDs are retained. [Report](../../validation/2026-10-17-coordinator-attachment.md).

This accepts the bounded read-only endpoint only. The unchecked abrupt-crash
attachment case, detached launcher, protected application APIs, two actual apps
and browser acceptance remain required. Task2 and whole-plan publication items
are not checked by this foundation; aggregate Tasks5–8 remain open.

Task2 implementation continuation: strict empty-body protected attach/status routes
use the server database only. The launcher prepares its reaper before detached
spawn; a retained native RED reproduces thread exhaustion after Popen. The fix
prevents spawn on reaper failure. An owned SIGKILL/restart test rejects the prior
session credentials over a real socket. A held incumbent without IPC is never
fenced/signalled by a client.

Schema19 adds immutable actor/project Stop grants only after successful physical
resolution. The new pause/cancel route returns four receipt fields and sanitized
revision conflicts, never a TaskCard. No backfill from arbitrary legacy IDs or
created_by is allowed. A failed migration preserves nonempty schema18 history.
Grant/task project identities have no mutation or deletion API; Store.command
rechecks project scope, revision/replay and fencing in its existing transaction.
Folder inspection and process/socket operations remain outside transactions.
Native application tests cover both review and integration, each with a present
and replaced source root, after the first real application process exits.
Initial64 pass/30.26s precedes the final reaper correction. Final composed185 and
frozen1158 IDs pass1150/8expectedskip,0fail/error;255 unchanged files,
1091.355s. Wheel133/CLI10/159 package files,FlyWire5,Ruff/mypy4 and actual
English Qwen pipeline84.158s/3model/34tools pass. Independent audits
and regular-file evidence are retained with the application validation report. UI buttons, CLI attachment and common
verification interfaces are separate required work; aggregate Tasks5–8 stay open.
