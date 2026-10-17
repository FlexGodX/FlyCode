# Internal coordinator attachment — 2026-10-17

This bounded increment adds a private authenticated read-only Unix endpoint and
client to the standalone coordinator published as `cf62960`. Client status checks
physical database pins, exact session/generation and a fresh challenge with
separate request/reply HMAC domains. Descriptor/socket/parent permissions are
owner-only. A short private socket path works with long native runtime paths.
Eight peer slots and one absolute header/body deadline isolate slow requests.
No client enters Host, recovers the queue, grants execution or closes its owner.

## Verification

- Final full suite:1142 exact registered/observed IDs,1134 passed/8 expected skips,
  zero failures/errors; 1085.932s and252 frozen files unchanged.
  Web73 ran after all non-web groups. Skips are opt-in graph/LM cases, not passes.
- Final attachment12 passed2.97s; independent client processes, substitution,
  wrong nonce/generation/challenge, native long path, slow/oversized peers,
  orderly Host restart, missing IPC refusal and startup cleanup are exercised.
  Prior combined62 passed101.45s before cleanup corrections; it is not the final
  broad proof. Final full includes the corrected bytes.
- Extracted wheel120 exact IDs pass;159 package files and README metadata match,
  ten CLI commands return0 with empty stderr. Wheel SHA
  `0c2f24d35679d2cdd0540e34fe3b88fa7c39881e67a610385347904c02b33c99`. Relevant Ruff and scoped mypy3 pass.
- Actual verified FlyWire5 pass/0skip. This engineering smoke supplies no neural
  orchestration or scientific admission.
- Actual English Qwen writer/review/integration succeeds in85.104s:
  3model calls/34tools, zero reserve/uncertainty, all three attempts stopped and
  source claims released. Owner tests/README/dirty bytes are preserved. Disconnect
  of the first read-only client preserves the same native Host for the second.
  No manual claim or owner command follows approval. One explicit primary probe
  plus three workflow requests match independent LM Studio timestamp/model/usage
  receipts; no retry/fallback or trained-checkpoint promotion occurs.

Source/test/static/config manifest:
`04a02cfe7ab4f70bd0dd414ed719468b988e5176c9c81280e4ce7406fd36568e` (252files).
Runtime research identity (a different file set):
`60c784c67592db954816533d97790e0edae234f272a56620869cc967c391bc48`.

## Retained failures and limits

The initial native ready coordinator lacked an endpoint. The first implementation
compared access time across a read and rejected authentic clients. A narrow real
RED reproduced that fault before stable identity/permissions/size/mtime replaced
access time. Independent review then found unready-start leakage and join of an
unstarted listener masking its original failure; actual RED2 precedes correction.
Sandbox socket refusal is retained as setup denial, not feature failure.

Independent final audit: 3561 checks, 698 stable input hashes;
SHA `15dd9ca0bcf867f6c625eb1a83b2e1a2aec9fd0e902a6723b511a14cffeb1ddf`. The audit reader's initial variable
shadowing exception is retained separately as a reader setup fault. Guarded
archive controls reject missing/symlink required inputs and changed frozen bytes;
a valid control seals regular files while skipping FIFO/symlink entries.

Closed evidence: `/private/tmp/flycode-attachment-validation-3cpligps` and
`/private/tmp/flycode-attachment-closed-focus`; regular-file archive:
`/Users/Administrator/.cache/flycode/validation/flycode-coordinator-attachment-3cpligps`.
Machine-readable report: [results](2026-10-17-coordinator-attachment.json).

This is a trusted same-UID local boundary, without isolation against hostile
processes of the same user or concurrent hostile filesystem atomicity claims.
Detached application launch, protected API/UI attachment and HTTP Stop across
applications remain open, as do shared verification and neural orchestration.
The earlier296-run feedback benchmark is historical, not current orchestration
admission. Aggregate Tasks5–8 and scientific §§90–99 remain open.
