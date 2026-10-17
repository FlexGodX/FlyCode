# Correctable candidate metadata — 2026-10-17

The previous English real-Qwen conflict case failed after both immutable pins
and the initial workspace had been read: five candidate prediction/rationale
fields were nested inside `edit_file.arguments`. Strict authorization rejected
that call before admission, debit or mutation, but the usage error escaped the
worker. The complete traceback and exact provider response remain retained in
[the input-gate report](2026-10-17-resolution-input-gate.md).

`Tools.authorize` still rejects that call. An internal `CandidateMetadataError`
subtype preserves the external `permission` code and adds actionable English
feedback listing the real tool argument names. Only the five observed candidate
metadata keys qualify. Before classification, the underlying tool call must pass
normal authorization, including paths/scopes. The temporary validation copy is
never returned or executed. Unknown argument keys, root overrides and actual
permission violations keep the original strict failure.

`AttemptEnvironment` converts only this subtype and existing missing-input
preconditions into failed transitions. The failed proposal has no file effect or
tool debit; the next generation can receive its diagnostic and submit a new
proper call under the same original budget. Blockers enter the canonical event
journal without storing argument values. Ownership/fence/deadline checks and
normal tool admission/debit are unchanged. There is no schema migration,
automatic field stripping, permission expansion or new lifecycle/store.

## TDD and targeted verification

The original RED selection has two genuine failures and three passing authority
controls: the direct environment raises instead of returning feedback, and an
actual HTTP/process worker exits `worker:StoreError`. The final full tool suite
passes29 cases in17.60s. It includes out-of-write-scope, traversal and root-override
cases that remain terminal, zero debit/effect on malformed metadata, preserved
working-memory diagnostics and exactly one corrected edit.

The real HTTP/process fixture observes the actual second request containing the
first error, two model calls under the original allocation, exact private
`answer = 1` and untouched owner `answer = 0`. It does not assert full verified
user delivery. An initial green run had28 passes/one failing test because the
test assumed three tools; the observed controller performs one edit. That log is
retained. The corrected assertion checks bounded original spending, exactly one
edit record and known outcomes; the separate environment regression proves the
invalid proposal incurs no tool debit. No production code changed to satisfy
that test assumption. Relevant Ruff and normal mypy47 source files pass.

## Frozen measurement

Implementation fingerprint:
`c8cf462fd4f666634d7aaa7e84400be89940258c37f8e286f040b36e56025c53`.
Archive: `~/.cache/flycode/validation/flycode-metadata-feedback-ya3x_hww`.
Source/environment identity matches before/after both measurement phases.

| Verification | Result |
| --- | --- |
| Full default suite,864 exact IDs once |856 passed,8 skipped;905.608s |
| Focused checks from extracted wheel |31 passed;86.76s pytest |
| Actual FlyWire graph |5 passed;10.28s pytest |
| Relevant Ruff / normal mypy | pass /47 source files pass |
| Wheel source / README metadata |133 exact Python files / match |
| Extracted CLI |10 controllers |
| Real-model user workflow |Not started: primary probe not approved |

Wheel SHA256:
`235f0e9ea210a21856e0e1184f2a43609529a781adaa2ce7a8675088105c7b36`.
The full run preserves fixed-Compose-project module affinity and compares the
collected/JUnit case Counters. Eight skipped opt-in cases are not passes. The
actual graph run is a separate opt-in measurement. Independent verification also
compares every wheel/source/test byte and rechecks all ten historical checkpoint
parameter/manifest hashes against the retained originals.

## Actual probe failures; no native completion claim

The built-in `ModelCatalogue.probe` calls the existing structured-primary client
and shared `HostTransport`. Its first request ends `APITimeoutError` after30.162s
under the original30s bound. The endpoint remains uncertain, preventing another
inference. No workflow, task, attempt, workspace, worker, tool, review, artifact
or integration is created. The post-check driver correctly stops before the
registered English two-writer/resolution case.

Manual diagnosis uniquely matches the provider's actual response by model,
request creation timestamp and response ID, retaining response bytes/hash and
usage outside Git. LM Studio records client disconnection, stopped generation
and a terminal generated response. Its partial candidate JSON is invalid and
produces no candidates through FlyCode's actual parser. The exact trusted
`HostTransport.finish` acknowledgment records failed, preserving the timeout
and actual673 prompt/103 completion/776 total tokens. It does not approve a role.

One separately registered explicit retry keeps the same model, source, English
prompt and30s bound, with no load/fallback or increased workflow budget. It also
ends `APITimeoutError` after30.108s. The uniquely matched terminal provider reply
is incomplete JSON:673 prompt/201 completion/874 total tokens. The same trusted
acknowledgment preserves failure and known usage. Both original failed reports,
the continuation protocol/driver, terminal responses and acknowledgment scripts
are retained. There is no third attempt or manual capability override.

Final accounting: two failed host probe calls,1,650 known tokens, zero running/
uncertain calls and zero user-workflow model/tool calls. Both descriptors have
no approved primary role. This is an unavailable current compatibility approval,
not evidence that Qwen can never support the protocol. The new native conflict
case is **not executed**, not passed; the current correction is verified through
real HTTP/process fixtures, not a successful current Qwen delivery.

## Separate unmerged prototype and remaining scope

A separate composition tests the earlier automatic-reservation prototype against
this source's other modules. Its source fingerprint is
`e90c6e915294ac7efbbe80ccf0d34423baf83f9f4c7b8e2a1198648231f2cc3b`;
only the three declared source overlays differ.83 compatibility cases pass; one
capacity case first fails because the launch command omitted required
`FLYCODE_CAPACITY_REPORT`. With that runner variable supplied, the unchanged
case passes4.30s. It covers64 tasks/165 synthetic zero-effect historical attempts,
one new claim,409 SELECTs, unchanged spending/deadline. The measured claim is
0.0293s under concurrent testing, not a dispatch SLA or a causal speedup claim.
All evidence is isolated under `isolated-autoclaim-prototype` in the archive.
The prototype is not in this published patch and does not implement a dispatcher.

The historical296 English benchmark runs,306 independent audits and10 trained
checkpoints remain separate. Both trained variants stay rejected; defaults and
biological topology are unchanged. Built-in benchmark/training/verification
remain the first choice, with retained failure evidence before external diagnosis.
Task5 dispatcher/child-wait, remaining Task6 recovery acceptance, Task7 scoped
orchestration research and Task8 canonical UI/API/CLI remain open. Current probe
failure cannot be turned into compatibility approval or a successful workflow.
