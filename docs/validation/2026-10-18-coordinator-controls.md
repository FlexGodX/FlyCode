# Tasks coordinator and Stop controls — 2026-10-18

The Tasks screen now explicitly connects the shared coordinator, independently of
live-event delivery. Loading the page only checks status. Connection neither
approves execution nor loads a model; client exit preserves the service.

`GET /api/tasks/stops` uses existing immutable schema19 grants and returns only
ID/revision/state/paused, with bounded keyset pagination. It issues no fresh scope,
grants or execution effects. Replaced/missing/tombstoned folders keep reads,
comments and resume denied. The browser clears inaccessible private detail and
shows minimal Stop controls after page reload and application restart.

Pause/cancel use the protected Stop route. A lost response retains the identical
operation/revision/key, including a completed cancel that has disappeared from
discovery. A definitive revision conflict requires another explicit click.
Command acknowledgement never claims the underlying process has stopped.

## Checked source and results

Parent `1ce53b6e10ff4f3309aaaf3f1e311107f1b73c05`. Source manifest
`149f3093064c260790bd1e92cf3aeb6504b6499a11840b42da5861cd373feca9` covers 263 unchanged Python/static/test/config files,
including browser scripts. Runtime identity `3cb8cf880e96d2540152c722c4d1819e297bf1ec84f6ac4c90b281743d1fa687`.
RU/EN README hashes are checked separately.

| Verification | Result |
|---|---|
| Complete registered pytest set | 1169 exact IDs; 1161 pass, 8 unchanged opt-in skips, 0 failures/0 errors; 1114.357s |
| Extracted wheel | 165 exact cases, 159 exact package files and README; 10 CLI commands |
| Static checks | Changed-file Ruff, mypy on 3 source files and diff check pass |
| Real FlyWire | 5 pass, no skip; no scientific promotion |
| Native browser composition | 6 cases; explicit connection, two apps, page/app restart, root loss, lost replies, conflict, review/integration pause/cancel |
| Packaged browser assets | Attachment, lost Stop and full Tasks regression pass |
| Existing interfaces | Classic/Desktop, local non-Git projects and independent projectless chats pass |
| Desktop/mobile | 1440/390/320 screenshots; keyboard connect/Stop; no horizontal overflow |
| Stop HTTP latency | 40.3–53.3ms; actual custody confirmation checked separately |
| Real English Qwen pipeline | 110.966s; 3 model calls, 34 tools; current integrated verdict, preserved owner bytes, zero reserve/unknown |
| Independent closed live audit | 2506 checks/946 stable input hashes; 597 verification artifact hashes/49 exact stopped receipts |

Provider timestamps, model IDs and usage agree with all four SQL model records
(one explicit primary probe plus three workflow requests). Two pre-repair failing
`run_tests` calls remain recorded; their structured error class is null. Final
success does not remove these earlier costs or outcomes.

## Failure evidence and limits

Actual REDs: missing protected discovery produced HTTP422; the original UI lacked
both the connection button and fallback Stop rows. Invalid persisted grant
capability/version raised unhandled validation errors before sanitized scope denial.
These failures and passing checks are retained. Initial runner import setup,
open-browser SSE shutdown in a one-second test fixture, and macOS path-alias wheel
preflight failures are kept separately; they are not feature REDs. Native lifecycle
cases leave the browser page before closing its actual app process. They do not
claim forced shutdown of an app with an indefinitely connected browser. An audit
assumption that every cancelled manifest must be complete was corrected: incomplete
cancelled evidence is explicitly non-passing, and every available artifact remains
hash-checked.

Raw evidence: `/private/var/folders/5v/q_zc0kvj07z83z4p9k2pfsvh0000gn/T/flycode-coordinator-controls-validation-h53ikcg8`. A regular-file archive at `/Users/Administrator/.cache/flycode/validation/flycode-coordinator-controls-h53ikcg8` contains
checks, scripts, screenshots, exact source bytes and failed attempts. The private
publication receipt records the final commit and archive checksum after publication.

This bounded increment does not close aggregate Tasks5–8. Canonical CLI,
verification plans/start/cancel/rerun/artifacts access, remaining execution UI and
neural orchestration admission still need implementation. Scientific §§90–99 and
negative historical Qwen checkpoint transfer remain separate. No trained
checkpoint, biological topology or default controller is promoted.
