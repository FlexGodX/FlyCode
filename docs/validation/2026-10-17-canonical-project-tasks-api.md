# Canonical projects and Tasks HTTP API — 2026-10-17

Status: validated bounded delivery; aggregate Tasks5–8 remain open.
Parent publication: `3a480b230991b5291543c33edb867b3660f6f269`.
Source: `da83bc70be5bdc9937aa7064d9d866a3ee9b800bba966174233751779b17f9b5`.
Evidence: `/private/tmp/flycode-canonical-api-validation-2rmy4w7t`.
Archive destination, sealed after checked push:
`~/.cache/flycode/validation/flycode-canonical-api-2rmy4w7t`.

## Delivered boundary

Schema16 adds immutable canonical project UUIDs and physical SourceRoot/ancestor
pins to the existing Store. Trusted registration resolves server-known local
selectors; equal selectors in different workspaces do not share authority.
Verified aliases converge. Root or ancestor replacement rejects previous scope,
including byte-identical copies. Old arbitrary project strings are never
implicitly imported into HTTP scope. Non-Git folders and legacy/projectless chat
history remain independent of the registry.

Internal revoke requires owner scope, revision and proven quiescence; live
attempts/calls or overlapping held source claims block it. An idle revoked
workflow relinquishes the one active slot without changing task contracts,
deadlines, attempt history or spending. Tombstones do not auto-reactivate.
SQL assignment/start-next guards deny revoked IDs while retaining trusted
historical low-level compatibility. Filesystem checks remain outside SQL.

Protected routes expose known-folder registration, canonical root/child drafts,
owner revision/idempotency commands, bounded literal comments, task history,
scoped keyset list/search/state/archive views and existing agents projections.
Each request reconstructs scope from actual allowed folders; JSON cannot issue
actor, role or project authority. Actor namespaces are stable per server
workspace/data directory. Private reads require the session header token plus
the existing loopback/origin checks. Drafts, comments and projections make no
model/tool request. Client text is data, not an execution instruction.

The SSE stream uses separate scan and delivered-history cursors. A fresh stream
starts with current state. Explicit Last-Event-ID/after resumes missed eligible
events before live events. Snapshot cursor bounds current board state;
replay_after/SSE ID bounds delivery. Historical events through the snapshot
frontier must not regress a board. Scope expansion resets scanning to the last
delivered cursor, preserving offline comments. Connections are opened/closed in
the same worker thread; no SQL transaction spans polling waits or inference.

Python web modules now join implementation identity. A real RED showed that a
web policy edit previously did not change its hash. Existing source15 validation
separately pinned all227 measurement files, including web code; that immutable
evidence is preserved rather than relabelled as current admission.

## Development evidence and failures

Registry tests retained missing contract/migration and revoke/claim REDs. Three
existing downgrade/re-upgrade failures exposed a migration compatibility issue;
additive IF NOT EXISTS behavior fixes it while preserving original assertions.
Registry/Store/projection compatibility72 passed27.94s in the isolated source.

Actual HTTP REDs cover missing routes, stale-revision response serialization,
SQLite cursor overflow, missing durable comment history, two missed comments
after reconnect, and scope expansion after a foreign global frontier. All remain
retained. Real loopback uvicorn tests verify delivery and owned-thread shutdown;
the finite mocked-disconnect test is not used as proof of actual replay.

Isolated web/registry/tracker92 passed109.75s before identity inclusion; subsequent
identity/API/tracker25 passed3.63s. Main composed compatibility98 passed112.41s.
Changed-file Ruff and scoped mypy65 passed with follow-imports=silent. Earlier
expanded checking reported22 diagnostics in11 unchanged dependency modules;
scoped success does not claim that wider import graph is clean.

The first full launcher rejected its obsolete978 count before spawning a test.
This preparation error is retained under gucs0ft2, not counted as a product RED.
The fresh registered runner reads the assignment count; its non-launching
preamble, source and harness hashes were checked before launch. No product
assertion or test deadline was weakened.

## Final frozen validation

All1017 registered default IDs ran exactly once:1009 passed/8 expected opt-in
skips in651.131s. Three non-web groups precede all71 serial web cases; four return
codes are0, Compose affinity and stage ordering are checked. The five enabled
real FlyWire cases separately passed5.256s. Other opt-in skips are not passes.

The extracted wheel passed282 exact related cases in336.931s process time.
All145 Python files and README metadata match frozen bytes; both README copies
match. Extracted CLI lists10 brains. Wheel SHA256:
`11bdf4ec8c19aa92af268d4b3d99d35b06f47f96dcfde9cb82d5b62d1263ca3c`.

The preregistered English internal pipeline passed55.721s: three stopped
successful attempts, four succeeded tasks, two stage receipts, a current
integration and preserved user bytes. It used3 model calls/34 tools, no manual
claims and no owner commands after approval. This is the existing internal
engineering route; it does not establish execution through the new Tasks API.

The explicit primary probe passed5.580s process time without loading/fallback.
Real legacy Stop passed14.976s, returned in0.001688s and retained1,333 late primary
tokens. Competing source admission remained blocked until confirmed settlement.
Independent audit checks all five known model outcomes, three released claims,
27 exact stopped-process receipts, zero uncertain outcomes/reservations, current
source evidence, preserved dirty bytes and all232 source/test/README/config
hashes plus protocol/registration hashes. Environment stayed unchanged.

## Remaining work

The HTTP adapter starts no dispatcher, recovery or inference. Background host
leadership for one shared DB, durable worker crash recovery, fresh physical
authority before runtime effects, execution/verification endpoints, UI board and
CLI commands remain open. A reviewed isolated physical-execution guard is not
part of this publication. User-facing removal/reactivation and historical-ID
import also remain separate work. No biological topology, checkpoint or default
changes, efficacy claim, orchestration admission or scientific promotion follows.
