# Owner pipeline commands implementation plan

Goal: expose explicit owner approval and graph changes through the existing live
Host, common CLI/HTTP/Tasks, without taking a second ownership lock or accepting
client roots, oracle paths, argv, roles, scopes or approval hashes.

Architecture: protected adapters issue project scope, resolve canonical project
and registered verification profile refs, stage exact oracle bytes into runtime
storage, then send typed authenticated commands to the existing Host. Final SQL
CAS pins the complete checked profile records, task/workflow revisions and
idempotency request; physical checks never run inside SQL. Existing low-level
recipe/history hashes and omitted-field behavior remain compatible.

1. [x] Guard trusted oracle import with an optional expected hash before any
   saved artifact, fd write or active head change. RED: altered bytes, modes,
   contract/checks and invalid hash reject without changing the prior head in
   normal and owned-fd routes. GREEN: exact imports resolve, retain check/file
   metadata and supersession rules. Run only oracle/registered-profile checks,
   scoped static checks and extracted-package verification before publication.
2. [ ] Add complete registered profile SQL pins to final parent/replan CAS;
   changed head/row/project after physical preflight must publish nothing.
   No `profile()`, Sources or oracle disk reads in the final transaction.
3. [ ] Add bounded typed owner command transport to authenticated attachment;
   execute using `host.connect(catalogue=...)`, never temporary Host/recovery.
   Read-only preview exposes blockers and IDs, excludes internal recipe paths.
   Explicit approval/replan resolves profile IDs/versions server-side; no grants
   or automatic binding creation through preview. Requests pin task/workflow
   revisions and retain exact idempotency semantics on lost replies/restart.
4. [ ] Share adapters through CLI, protected HTTP and explicit Tasks confirmation.
   Check token/origin/scope, rejected fields, stale revisions, replay, keyboard,
   desktop/390/320 and Stop. Keep prior screens and saved materials usable.
5. [ ] Execute real public parent preparation/wait, branch retry, separate owner
   replan/new branch, restart, fresh review, continuation and current integrated
   completion under original spending/deadline and max3/4pool/64tasks. Save all
   failures. Full neural/research and aggregate final gates remain separate.

Preserve the parallel brain changes. Develop against pinned public source in a
private directory; publish only exact checked own bytes. Feature-scoped tests
only after each change. One active workflow, 60model/120tool/1800s,20% verification.

The first checkpoint only guards import activation; it does not expose public
owner commands or close dependent-execution Tasks4/5 or the aggregate goal.

Checked prerequisite: [report](../../validation/2026-10-19-owner-import-pins.md).
The existing registered-plan SQL helper is strengthened; Task2 still requires
parent/replan final wiring. No owner command or permission is exposed yet.
