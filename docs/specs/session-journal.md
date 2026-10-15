# Session journal: DeepSeek Harness reference and FlyCode implementation

Reference inspected: public `deepseek-ai/deepseek-harness`, commit
[`86a402200d95fecef4529d969836f1fe13c6cb0d`](https://github.com/deepseek-ai/deepseek-harness/tree/86a402200d95fecef4529d969836f1fe13c6cb0d).
The source was read locally without running its application. This is an independent
implementation using FlyCode's existing event journal, not a port of the plugin runtime.

## Source observations

| Upstream source at the pinned commit | Observed behavior | FlyCode equivalent |
|---|---|---|
| [JSONL persistence](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/session/session-persistence-jsonl/README.md) | Canonical ordered events, physical storage independent of logical reads | Existing `events.jsonl`, IDs and SSE replay remain authoritative |
| [Query filters](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/session-query/session-query/src/filters.ts) | Event metadata and literal text predicates preserve order | Case-insensitive literal search plus source/error filters in the selected chat |
| [Export archive](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/session-query/session-log-export/src/archive.ts) | Serialize committed events, flush live session before export, stream bounded archive chunks | Snapshot the recorded prefix under lock; stream JSONL outside the lock |
| [Telemetry coordinator](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/session/session-telemetry/src/coordinator.ts) | Capture/reporting separated from canonical history; receiver deduplication uses event identity | Journal viewing/export requires no model call or external reporting; duplicate SSE IDs ignored |

## Delivered behavior

Both `/` and `/desktop` share the new Journal view. It shows chronological event
IDs, local times, available step numbers, model/tool names and failure highlights.
Filters cover models, tools, controller and session activity; text search includes
the complete recorded envelope and payload. Error filtering includes failed tools,
model errors and failed runs. A failed tool during eventual successful repair remains
visible as an error. Unknown event types remain visible under Session.

Initially render the newest 100 matching events; “Show earlier” adds 100. Payload
JSON is created only for expanded rows. Expanded rows and keyboard focus survive
live updates while the row remains visible. Follow can be disabled while inspecting
older events. Switching chats resets the journal filters and follow preference.

`GET /api/runs/{run_id}/journal` downloads the complete recorded event prefix as
`application/x-ndjson`, regardless of browser filters. The immutable prefix is captured
before response iteration; subsequent events belong to a later download. It uses the
same local Host/Origin protection as history, rejects unknown run IDs and never takes
a filesystem path from the client. It does not buffer the serialized download in the
browser. Rendering uses text nodes, including for tool output containing HTML.

## Boundaries

The existing journal bounds strings at 16,000 characters and collections at 300
items. Export preserves these recorded values; it cannot recover truncated output.
Model events contain the metadata already emitted by FlyCode, not complete HTTP
requests, system prompts, hidden reasoning or token usage. No ZIP attachments,
subagent tree, Zstandard storage, SQLite search index, model-facing query tool,
OpenTelemetry backend or crash-proof telemetry delivery is claimed here. Existing
training telemetry (`steps.jsonl`/`runs.jsonl`) retains its separate schema and use.

## Verification

`tests/unit/web/test_journal.py` covers the actual API, persisted restart, ordered
JSONL, foreign-origin rejection, unknown IDs and export/writer concurrency.
`tests/browser/journal.cjs` runs real demo repair and SSE replay in both interfaces,
checks search/source/error filters, details, download and mobile 390/320 layouts,
and exercises duplicate/unknown events, lazy pagination, focus and HTML text safety.
Only interface/API/browser tests are run for this change.
