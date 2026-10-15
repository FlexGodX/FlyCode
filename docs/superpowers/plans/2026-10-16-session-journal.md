# Session journal implementation plan

**Goal:** Make existing FlyCode run logs searchable, inspectable and downloadable in both interfaces.
**Architecture:** Keep `AgentEvent` JSONL and SSE as the canonical stream. Add a browser projection with source/severity filters, lazy details and bounded rendering, plus a streaming export of a fixed event prefix. No inference or external telemetry for journal actions.
**Spec:** `docs/specs/session-journal.md`; Plan §§66, 75.

1. Add API acceptance tests for JSONL export, restart, unknown IDs and foreign origin; observe missing route failure. Implement export with a snapshot under the manager lock and serialization outside it. Verify targeted API tests.
2. Add browser acceptance for live events, search, errors/source filters, details, download, replay, duplicate events and bounded rendering; observe missing controls failure. Implement `journal.js`, shared styling and existing app hooks. Verify classic and desktop at 1440/390/320 px and inspect screenshots.
3. Record upstream evidence and implementation boundaries, update RU/EN instructions and handoff. Run interface regressions, review scoped diff, commit and publish only verified journal changes.

Constraints: preserve legacy event bytes/schema, existing views and unrelated working changes. Export is the complete recorded prefix, independent of UI filters. Render at most 100 rows initially and reveal older matches on request. Unknown event kinds remain inspectable. Filters reset when selecting another chat. Empty search results never imply the journal is empty.

Validation completed: 44 UI/API tests passed; journal browser acceptance passed in both interfaces, including 390/320 px and dark theme; classic and desktop browser regressions passed. The isolated staged snapshot passed 21 API tests and the journal browser scenarios. Ruff/mypy and JavaScript syntax passed. No unrelated test suites were run.
