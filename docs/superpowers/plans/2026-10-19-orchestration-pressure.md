# AC-14 orchestration pressure implementation plan

Status: distinct-agent race, native4/fifth capacity and actual60/120 service gates measured; combined pressure remains open. Parent: e24067e2ae6e23240a93a98d52f22b24f00f2e58.

Goal: demonstrate bounded simultaneous agent requests, question/answer, retry and owner replan through actual Host/runtime interfaces, without recursive task creation, endpoint overlap, stalled Stop or budget reset. Existing small successful flows and arithmetic ledger tests do not close literal saturation gates.

The continuing specification goal authorizes implementation and scoped verification. Preserve the parallel brain work; develop in this immutable-public-source copy. Use English model requests/objectives/messages. No production change is justified until an actual feature failure is reproduced. Keep scientific, biological and final aggregate gates distinct.

## Requirements and evidence

- AC-14, AC-04/05/07/08/13; TRK-05, TT-03/04/05/11/16; delivery Tasks4/5/6/8.
- Defaults: one active workflow,4instances,64tasks,3attempts per task,60modelcalls,120tools,1800s and20% verification reserve. Do not lower these defaults and call it literal default saturation.
- No SQL transaction during HTTP, subject inspection or subprocess execution. Independent writer observations use a separate Store connection in that thread.
- Every model/tool effect must have an actual registered attempt/fence/permission/budget debit. Never simulate workload by assigning Store.spend totals or fabricate process-start acknowledgements.
- task_request message text remains data. Only an authenticated request_task may enter bound admission, and only explicit owner commands may change the task graph.
- Source and tests stay frozen during each measured run. Report all failures, assertions and cleanup outcomes; pass is set only after owned workers/process groups and HTTP settle.

## Approach

The existing browser flow proves the owner controls and sibling exchange, but extending every pressure permutation through UI adds timing ambiguity. Use actual HTTP/Host/runtime admission for deterministic pressure and retain browser Stop/mobile regressions separately. Both paths use the same canonical services; neither bypasses permission, execution binding or verification.

1. Read exact helpers and define a bounded event/ledger sampler. At every admission record active ownership, tasks, attempts, spent/reserved/free, endpoint concurrency, deadline and current revisions. Verify sampler against actual SQL schema and existing records without launching workload.
2. Add a two-distinct-instance pull race against one bound task. Use separate real Store/ModelCatalogue connections, issued contexts and simultaneous requests. Verify one attempt/reservation/event, original binding and one no-work/replay outcome; same-context duplicate alone is not this proof.
3. Hold four actual agent workers in owned tool subprocesses; submit a fifth request and prove no fifth live worker/assignment can bypass the safety mask. Stop one through the canonical owner path, confirm its group is absent, then permit the fifth. Preserve the existing independent reward progress contract in the separate frozen-controller check.
4. Exercise actual default model/tool saturation and the verification reserve. Developer effects cannot spend protected capacity; approved verification can use the remainder. Verify60 actual workflow HTTP and120 actual tool executions with their own receipts, then reject61/121 without dispatching an external effect. If the existing runtime cannot naturally reach this route, report that gap and reproduce it before proposing a product change; manual budget arithmetic is not a substitute.
5. Verify64 persisted tasks and refusal65, three actual stopped attempts and refusal4, and the fixed1800s deadline with a controlled clock. Explicitly distinguish capacity-only graphs from verified completion. Do not pad a running approved pipeline with unapproved tasks or reset history to satisfy a counter.
6. Combine the real question/answer route with a rejected first writer artifact, one approved retry and its second negative review. Concurrent bounded owner replan/agent-request duplicates must reject while ownership is unconfirmed, remain responsive and preserve spend. After confirmed quiescence, one owner replan may add a bounded helper and admit the third author attempt; exact replay must retain one graph generation and the same IDs. Keep rejected artifacts/reviews/messages for audit, and do not retarget old endpoints into new attempts.
7. Automatically finish fresh reviews, parent continuation and current integration through Coordinator.run. Verify exact source/dirty bytes, immutable deadline, settled reservations, absence of false completion and a stable idle command/model count. Record whether this combined flow itself reached each ceiling; separate component gates do not silently establish a saturated combined run.
8. Freeze exact package/tests; run only relevant scoped tests, Ruff/mypy, native HTTP/subprocess and browser regressions. Check the extracted wheel once through pytest pythonpath (no duplicate PYTHONPATH distribution). Merge into a new frozen snapshot of current brain work and verify the overlapping runtime contracts. Archive regular-file evidence and publish checked changes with README RU/EN, Plan and handoff updates.

## Existing gates to reuse accurately

- test_store::test_four_active_instances_and_disjoint_scope_admission: SQL assignments, not four running workers.
- test_registered_coordinator::test_shared_coordinator_never_admits_fifth_registered_check_until_a_slot_settles: four actual verification subprocesses, not four agent workers.
- test_store::test_default_task_count_and_depth_limits_apply_to_real_draft_graph: real64-node persisted draft graph, no inference.
- test_pipeline_retries::test_two_owner_approved_retries_stop_at_global_three_attempts_and_keep_all_failures: actual workers/checks, manually ticked dispatcher and5model calls.
- test_store::test_verification_can_use_protected_reserve_after_developer_consumes_its_share: arithmetic48/96 spend and12/24 reserve, not60 HTTP/120 tool effects.
- test_store::test_workflow_deadline_revokes_even_healthy_worker_and_retains_slot_until_stop: controlled clock/heartbeats, no30-minute wait required.
- message_automatic_runner.py/message_scenario.py: existing7task/4instance/8attempt question-answer completion;10workflow models and55tools. It has no negative retry/replan pressure and does not prove default exhaustion.

Measured components: steps2 and native capacity part3 pass; step4 real Host service saturation passes. Capacity gate releases one real held test and verifies confirmed exit before fifth admission; it does not cancel a held pytest or claim its unknown effects settled. Early simultaneous requests may correctly close no-work during an assigned admission; pending fifth proof issues the request only while four workers are actually held. Step4 uses96 real reads plus24 owner-registered custody pytest invocations and60 SDK HTTP requests. It does not claim these were all autonomous native worker decisions. Combined step6/7, scientific and final aggregate gates remain open. See ../../validation/2026-10-19-orchestration-pressure.md.

Checked package:189 files,7 scoped extracted checks58.98s; Ruff, UTF8 README and CLI parity pass. Regular-file evidence and all earlier harness failures retained; combined/scientific/final goal gates stay open.
