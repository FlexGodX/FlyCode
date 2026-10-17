# Registered execution authority delivery plan

**Goal:** reject stale physical-project authority before canonical dispatch,
worker launch and integration effects, preserving historical trusted low-level
IDs and the existing stop/accounting contracts.

**Architecture:** reuse `Projects.resolve` at existing outside-SQL admission and
source-guard boundaries. No new dispatcher, process manager or permission system.
The canonical UUID must resolve to the actual root/ancestor identity; matching
source bytes do not substitute for that authority.

**Spec:** Plan §§100/103/104, ORC08/09 and the aggregate runtime/integration work.
Existing4-instance/64-task/3-attempt/60-model/120-tool/30-minute limits and20%
verification reserve remain. No filesystem or inference inside SQL transactions.

**Files:** `orchestration/{dispatch,runtime,integration}.py` and
`tests/unit/orchestration/test_registered_project_execution.py`.

- [x] Isolated RED: byte-identical root/ancestor replacement still permits
  assignment/launch; registered task can launch another registered folder.
- [x] Add the smallest shared check before dispatch, between claim/launch, before
  and after private workspace capture, and at integration baseline/effect guards.
- [x] Actual post-claim baseline RED: replacement source was copied after claim;
  recheck authority before copying its admitted baseline.
- [x]13 new tests pass22.42s, including real local mock-HTTP worker/review/
  integration and preserved user bytes. Historical IDs retain low-level behavior;
  no missing registry row grants HTTP authority.
- [x] Isolated83 exact focused compatibility cases pass145.08s, Ruff4 and mypy52.
  Preserve all fixture/probe setup failures separately from functional REDs.
- [x] Verify immutable patch/baseline hashes and compose in main after APIb479926
  publication/archive, then run focused tests, Ruff/mypy and package-byte/CLI checks.
- [x] Update Plan/handoff/report and publish only checked bytes. Parent API full
 1017/wheel282/native results describe the parent source, not this changed source.

Already admitted worker tools may continue/spend in their private copy after
external replacement of the original folder. They gain no replacement-source
authority. Loss of registration can block rollback into the original pinned
folder and retain a reconciling result/held source claim until trusted repair.
No forced unlock, hostile filesystem transaction, durable worker-crash proof,
host daemon, shared execution UI or neural admission is claimed. Tasks5–8 remain
open; worker custody is a separate isolated follow-up.
