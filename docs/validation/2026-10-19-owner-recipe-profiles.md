# Registered profiles at recipe publication — 2026-10-19

Trusted `Pipelines.approve_parent(..., profile_pins=...)` and `replan` now bind
exact registered verification records for every existing task and `new:N` for
each added pair. The bounded helper requires project, acceptance contract,
approval, trust and reruns to match, then inspects the current registered profile
outside SQL. The final writer transaction rechecks the complete saved profile,
current approval head and canonical project before publishing or allocating IDs.
No physical inspection or inference runs in that transaction.

With pins, idempotency includes profile ID and the full canonical record hash;
exact replay returns the prior receipt before new preflight, without a new
attempt. Without pins, old request/recipe/history bytes remain unchanged.
The field is a trusted Python API only. New public adapters must resolve exact
server profile refs and always pass complete pins; they are still unimplemented.

The actual six-attempt route performs pinned approval, a stopped parent partial
checkpoint, owner replan, fresh child/parent review and current applied integration.
It retains original spending/deadline and owner dirty/untracked bytes. Final DB:
3 attempt model calls plus1 separate probe,
36 tools, reserves0/unknown0; one graph revision.
The workflow succeeds, and the integration remains current in the native test.
Other cases intentionally remain waiting or change metadata; they are not claimed
universally terminal or reserve-free.

Final extracted wheel: **14 passed38.14s**, exact collection/no skips. Relevant
existing compatibility28 pass150.13s. Frozen combined207 source files:14 pass43.25s,
interoperability only. Ruff4/mypy3, package178 source/static files,318 unchanged
inputs, UTF-8 README metadata and extracted CLI help pass. Wheel SHA-256:
`2337314a3ae18715ed06d54ee202b490c735bd35f5df8d4fa6e6b57e6f57d840`. Counts overlap and must not be added.

| Boundary | Exit | Cases | Seconds | Meaning |
|---|---:|---:|---:|---|
| red | 1 | 1 | 1.037 | Actual missing profile_pins API keyword, one case; no claim of a preexisting accepted race execution. |
| green | 0 | 10 | 8.944 | Initial ten feature cases pass, including real canonical Host and second-connection profile row/head races. |
| complete | 0 | 13 | 11.888 | Thirteen cases pass after adding project-record races and exact replay after profile supersession. |
| delivery | 0 | 1 | 29.320 | One additional complete native route passes: pinned approval, stopped parent, owner replan, fresh checks and current integration. |
| compat | 0 | 28 | 150.126 | Twenty-eight relevant existing parent/retry/replan/branch/outbox and previous import/profile cases pass; no-pin contracts remain unchanged. |
| combined | 0 | 14 | 43.249 | All14 feature cases pass against frozen207-source combined checkout; own public package excludes parallel brain changes. |

The RED is an actual missing API keyword; no previously executed accepted race
is inferred. The final tests inject second-connection row/head/project mutation
after the last successful registered-profile physical read, before final SQL.
The initial test draft injected after runtime oracle reads; it was moved to this
later registered-profile boundary before measurement of the new implementation.
No test setup, failed correction or static errors occurred in this increment.

Read-only independent review found no blockers. It did not execute tests; native
and package evidence are separate. All source deltas match the checked package.
The model is a deterministic local HTTP server with English objectives; checks
and process groups are real. This is no live LM/biological-quality claim.

Remaining [owner command work](../superpowers/plans/2026-10-19-owner-pipeline-commands.md):
no-follow bounded oracle staging and expected hash activation, typed authenticated
live-Host transport, explicit preparation receipt, protected CLI/HTTP/Tasks controls,
and combined retry/new-branch/restart acceptance. No UI, full-suite, live graph/LM,
training or promotion run here. Aggregate/controller/neural/research/final gates
remain open; this checkpoint does not complete them.

Evidence archive: `/Users/Administrator/.cache/flycode/validation/flycode-owner-recipe-profiles-checked-qw1we2n3`; 10975 regular files,
99253480 bytes. Manifest SHA-256 `88a03a134482f68a0c3d1f0b77fb509e5bcf4a112772781ec9de7764affa0173`.
Source and destination regular-file hashes plus inventory were verified. Original
custody paths/identities are retained; the copy is not portable execution authority.
Publication identity follows in a separate record after push.
