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
2. [x] Add complete registered profile SQL pins to final parent/replan CAS;
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
The existing registered-plan SQL helper is strengthened. The next checked
checkpoint wires complete profile pins into parent/replan final publication;
no public owner command or permission is exposed yet.


## Parent/replan pin wiring

Trusted `profile_pins` supplies immutable registered verification records for all
existing tasks; `new:N` supplies the single shared contract for each added pair.
The bounded helper verifies project/contract/approval/trust/reruns and current
physical profile outside SQL, then full profile/head/project inside the final
writer transaction. The request binds profile ID plus full canonical record hash;
missing pins preserve legacy request/recipe bytes. New public adapters must
always pass complete server-resolved pins; omission is only for existing trusted
low-level callers. Exact replay returns the old
receipt before fresh preflight and never grants a new attempt.

The actual complete native route now includes pinned initial approval, a stopped
parent checkpoint, owner replan, fresh child/parent review and current applied
integration. This still does not replace the combined retry/new-branch/restart
public-command acceptance from Task5 above.

## Next transport details

Use a separate bounded owner command frame and preparation semaphore on the
existing attachment service; retain the status path and current Host connection.
The server selects a stable local owner actor and issues project scope after
physical `Projects.resolve(..., actor_id=None)`. Preview must not create a Stop
grant, bindings, oracle heads or execution. Outer HTTP/CLI adapters still restrict
selected project IDs before forwarding; never accept actor/role/scope/paths.

Source binding needs an explicit preparation operation with expected task/current
binding refs and server-resolved roots. Its receipt says prepared, with no queue
or attempts; approval is a separate explicit mutation. This distinguishes partial
preparation from atomic recipe publication and allows exact retry after a lost
reply. Preview lists missing criteria/grants/budget/profile/binding; it grants none.
Client preparation may select supported controller settings, but never an import
path, runtime directory, source path or model fallback. Keep status/Stop responsive
while bounded preparation runs. Registered oracle import must stage bounded exact
bytes through owned no-follow handles and compare expected fingerprint before
runtime head activation. A later SQL conflict may leave a cached oracle; do not
restore an old head over a concurrent activation.

Checked Task2 [report](../../validation/2026-10-19-owner-recipe-profiles.md): final wheel14,
compatibility28 and frozen parallel-source14 pass. Task3 live transport/staging,
Task4 public controls and Task5 combined acceptance remain unchecked.
