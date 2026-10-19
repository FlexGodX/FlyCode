# AC-14 orchestration pressure implementation plan

Status: distinct-agent race, native4/fifth capacity and actual60/120 service gates measured; combined question/retry/replan measured; saturated combined pressure remains open. Parent: e24067e2ae6e23240a93a98d52f22b24f00f2e58.

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


## Next combined measurement, parent1a68001

Use tests/browser/message_pressure_runner.py with the canonical managed fixture and original English question/answer gates. Before launching, execute --prepare-only: all seven tasks, four instances, owner-registered verification pins, final source bindings, bounded retry policy and explicit routes must be valid; zero worker/tool/attempt launches and one separately recorded catalogue probe. Assert loaded package location and unchanged source/test inventory.

The first A writer answers B, then changes0→-1. B derives2 from the actual answer. Automatic bounded A retry inherits -1 and changes it to-2; two independent negative reviews must remain. Owner graph replan twice during held live workers must reject stop_unconfirmed. An idle registered agent's request_task must not recursively create work. After all actual owners stop and the parent has its final failure blocker, retire old message routes, perform one owner graph revision plus exact replay, and leave automatic dispatch running. Compare settled prior-attempt reservation rows to prove no refund; new attempts may legitimately spend concurrently. A global queue pause forbids replan. Third A starts from original0 and repairs to1. Expect12 attempts and current merged integration; measure actual model/tool counts, never infer literal saturation from component gates. Original negative local gate outcomes remain in evidence.

This is common Host service/automatic-runtime acceptance, not an additional browser or scientific result. No production changes until a real failure identifies a missing behavior. All outcomes remain open until inspected.


Retained combined failures: at8192 configured tokens the retry correctly refused artifact_context_budget before another HTTP; repeat uses an explicitly loaded/profile-pinned16384 context, not dropped diagnostics. At16384 the first harness incorrectly paused the global queue before replan and received paused. After removing that pause, a legitimate parent-blocker update raced physical preflight and caused revision; wait for canonical parent child_failed/child_review_failed before capturing owner versions. All source/test fingerprints remain unchanged per measured run; no production guards were relaxed.


Original public-source combined run passes85.90s:7tasks/12attempts/14workflow HTTP+1probe/61tools, one graph revision/current integration,6native+48registered custody groups stopped; source/tests unchanged. Four failed local gates and all prior failures retained. This does not close saturated combined AC-14. Final exact extracted package passes96.15s; frozen243-file brain composition passes102.65s using heuristic_fsm. Seven scoped frozen cases pass65.46s; all source/test fingerprints unchanged. The80s cleanup cutoff, premature fixed idle observation and incorrect AccessScope.session harness field are retained as failures. Final harness bounds completion at180s, leaves workflow1800s, admits only bounded null Host selection settlement and then checks strict idle/currentness/source. No configured-neural/scientific or saturated combined acceptance is inferred.

## Checked64-task capacity extension

Native64-task capacity,2026-10-19:28pairs/7groups/integrator approved with
all owner pins and final bindings;65thtask refused. Four actual held agent workers
block the fifth original issued request until confirmed exit. Two live owner
replans refuse stop_unconfirmed. Natural gate release and one negative review
block22dependents;4other reviews pass.11attempts/11workflow HTTP+1probe/29tools,
6native+18registered pytest groups stopped, source/deadline preserved, reserve0/
uncertaintyfalse, no endpoint overlap or false completion. Final extracted189
files pass62.50s; frozen244-file brain composition passes66.35s
with heuristic_fsm. Ruff/package/UTF8 README/CLI pass; no full/UI rerun. First
pause/unknown-outcome and20-versus18launch harness failures are retained. This
is capacity with negative review, not combined question/retry or budget
saturation/scientific admission. Overall goal and remaining AC-14/final gates open.
[Report](../../validation/2026-10-19-hierarchy-capacity.md).

## Combined budget boundary and dispatch lock regression

Combined native budget boundary, 2026-10-19: the approved 64-task graph reaches
48 developer HTTP calls and 120 actual tools, including 74 registered pytest
processes. Four native workers overlap; the issued fifth waits for confirmed
exit. Two negative reviews and one exact owner-replan replay retain history.
The final review reserves two tools, then its third check is cancelled with
policy budget_exhausted before spawning. No integration or false completion;
83 process groups stopped, source/deadline preserved, reservations zero and
uncertainty false. Public-source run passes 149.82s. A real SQLite contention
failure exposed 197 repeated graph guards per dispatch; transaction-local reuse
reduces this to one, with semantic/binding tamper still blocked. Three new checks
and 88 related queue/admission checks pass; Ruff and Store mypy pass. Exact189-file wheel passes136.88s; frozen246-file brain composition passes191.88s with heuristic_fsm. Both retain48/120 and83 stopped groups. Three new cases pass in each; the prior capacity regression passes29.93s. Wheel/UTF8 README/CLI parity pass; no full/UI rerun. Controlled provider state retains
B's parsed answer; this does not measure model recall or neural intelligence.
Overall goal, scientific and final aggregate gates remain open.

The native scenario is tests/browser/saturation_combined_runner.py. Its prepare-only path pins loaded context16384 and all64 profiles before any worker/tool effect. Source/test bytes remain fixed per run. Preserve the first scheduler-ordering failure, two missing-result/SQLite failures and diagnostic rerun; the measured admission writer transaction held5.72765s. The regression test observes197 full guards instead of one, then passes after local checked-context reuse. Standalone claims retain their own guards; do not increase SQLite timeout or remove authority checks. Check the final extracted wheel and a fresh frozen composition of parallel brain changes before publication.
