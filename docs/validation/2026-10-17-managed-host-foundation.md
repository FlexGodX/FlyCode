# Managed host foundation — working validation, 2026-10-17

This uncommitted increment builds on public `6c6d33e`. It implements physical
leadership and admission for existing canonical runtime services. It does not
implement an independent background coordinator or complete aggregate Tasks5–8.

## Behavior

Schema18 adds a single host registration and session. The registration pins the
physical database, directory ancestry, runtime root and exact kernel lock to an
external path reference. Runtime-root selection cannot bypass an existing pin.
Hard-linked databases are refused before SQLite/WAL open. Missing, substituted
or one-sided initialized ownership requires trusted repair.

One non-inherited held descriptor grants one immutable session generation.
Diagnostic heartbeat age and saved PIDs do not grant takeover. First adoption
refuses active older manual attempts without changing their fences or accounting.
Closing a host revokes its session and reconciles active attempts atomically;
it does not invent process-stop or remote-effect confirmation.

Fresh claim/dispatch/start/recovery, workers, checks, models and tools require
current authority. Ordinary child tools use the owning parent's RPC for admission
and accounting. Registered pytest checks revalidate before their actual GO.
Artifacts revalidate after capture and inside their acceptance transaction;
review approval and original-source application also require current authority.
Raw stopped-worker output and real verification evidence remain retained when
canonical acceptance is refused.

Exact late acknowledgements retain actual usage even after closure, lost physical
authority or malformed registration/session JSON. Invalid metadata denies new
work. This does not turn an unknown external effect into a known outcome.

## Measurements

| Source and scope | Result |
|---|---|
| Broad orchestration group before final metadata correction | 560 passed, 1275.56s |
| Isolated final Host cases | 44 passed, 31.89s |
| Isolated final model-resource/tool cases | 62 passed, 33.50s |
| Main composed final Host/resource/tool cases | 106 passed, 68.67s |
| Ruff: domain contract, orchestration package and its tests | Passed |
| Scoped mypy: 12 changed production modules | Passed |

The final106 cases overlap the earlier560; these counts must not be added as
unique coverage. Independent audits compare exact test IDs. The earlier53-file
hash capture was made after that group started, with no subsequent source/test
edits during it; it is not represented as a pre-start freeze. Final53 source/test
hashes match the preceding parent and isolated correction manifests.

No current-source default full suite, extracted wheel, biological graph,
already-loaded real-model or browser acceptance is claimed. Real HTTP here uses
the test server; native subprocess checks execute actual worker/pytest processes.

## Retained failures

Actual REDs include bare managed-admission bypasses, final subprocess GO after
lock replacement, accepted artifacts after authority loss at entry and after
preparation, lost late HTTP accounting during concurrent close, and silently
recreated missing/foreign sessions. Four further actual HTTP REDs reproduce
malformed metadata before acknowledgement and inside its transaction.

The first final-boundary correction run retains33 passes and2 failed test
expectations: the model reservation was already spent, while a known never-started
pytest correctly failed instead of remaining uncertain. The corrected assertions
compare actual remaining reservations and the exact stopped receipt. Earlier
clock/setup/fixture mistakes remain separate from genuine feature REDs.

Local records:

- `/private/tmp/flycode-host-foundation-orchestration.xml` and matching audit JSON.
- `/private/tmp/flycode-host-main-postfix.xml` and exact collection.
- `/private/tmp/flycode-host-foundation-status.json`: final hashes and scope.
- `/private/tmp/flycode-host-late-metadata-red.xml`: four actual HTTP failures.
- `/private/tmp/flycode-host-metadata-lab.json`: isolated parent/final hashes.
- Regular-file raw RED copies under `/private/tmp/flycode-host-*-red-evidence`.

Closed final106 evidence is copied to
`/private/tmp/flycode-host-main-postfix-evidence`:935 regular files independently
rehashed;66 non-regular entries are omitted. Manifest SHA256:
`a62b28c6725a922237a14f18acb7f47445812bde97955a807ede83f0126edb79`.
Copied historical paths/receipts do not grant new execution custody.

## Remaining delivery

Implement the separate coordinator lifecycle, thread-local execution Stores,
responsive control, saved-custody reconciliation and web/CLI attachment. Run the
actual managed writer→review→integration completion scenario, then freeze and
verify publication bytes through the full suite/package/graph/model/UI checks.
Neural orchestration admission, its research matrix and scientific §§90–99 remain
open. [Execution plan](../superpowers/plans/2026-10-17-managed-coordinator.md).
