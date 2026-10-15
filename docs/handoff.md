# FlyCode handoff brief

Authoritative roadmap: `docs/Plan.md` (§1–99). Operating rules: `AGENTS.md`.
The baseline MVP covers Phase 4 (§78). The latest extension connects two local LM Studio models under controller scheduling.

## Current extension: local Qwen + Jev

Implementation plan: `docs/superpowers/plans/2026-10-15-lmstudio-dual-model.md`.

- `--llm lmstudio` selects `qwen/qwen3.5-9b` for candidate generation and
  `jev-style-qwen3.5-2b-decision` for native single-token option scoring.
- Both exact IDs were verified through the local LM Studio API and both models
  were loaded. No weights were downloaded, changed or trained.
- Routing consumes CortexContext.neural_state. Blocked states skip inference;
  verified completion and simple non-Compose VERIFY steps are local; confidence
  >= 0.8 and hard control skip Jev. Safe candidate scoring is advisory, and the
  existing safety mask and brain tournament retain final control.
- Qwen uses structured JSON, a 1536-token response cap and disabled reasoning
  output. Jev uses the installed v1 protocol with one token and top-20 logprobs.
  Missing/nonfinite decision distributions produce an explicit diagnostic and
  preserve Qwen proposals. Qwen errors stop without an invented repair.
- Requests have no automatic retries and a per-run shared call budget. Failed
  requests count. The local default timeout is 120 seconds: on this host a
  366-token edit proposal took about 58 seconds at roughly 7 tokens/second.
- Configure `--base-url`, `--model`, `--decision-model`, `--request-timeout`,
  `--max-model-calls`. Optional authentication is `LMSTUDIO_API_KEY`; the endpoint
  can be set with `LMSTUDIO_BASE_URL`. There is no cloud fallback or inherited
  proxy routing for local inference.
- Per-model counts/errors are recorded in RunMetrics, and each executed step
  includes a model_routing trace. Consolidation makes no additional request.
- The SDK floor is now `openai>=1.61` for the transport and request interfaces.

Verified on 2026-10-15:

```text
python -m pytest -q -> 206 passed, 1 skipped in 89.52s
  (the single skip is the explicit opt-in local-model acceptance)
FLYCODE_TEST_LMSTUDIO=1 python -m pytest tests/e2e/test_lmstudio.py -q
  -> 1 passed in 148.09s
Real CLI toy repair -> success=True, 6 steps; Qwen=3 calls, Jev=3 calls;
  no model errors; post-edit tests and STOP required no inference
Targeted Ruff on new routing/clients/CLI/telemetry/tests -> all checks passed
Repository-wide Ruff -> 57 existing findings
mypy src/flycode --ignore-missing-imports -> 20 errors in the same 8 files
```

Local inference and the wiring are verified. No improvement over the baseline
has been established, and this extension does not implement a real biological
connectome or satisfy Phase 5 acceptance.

## Phase status

| Phase | Plan | State |
|---|---|---|
| 0 SoftwareGym | §74 | Implemented and tested |
| 1 Autonomous agent | §75 | Toy repositories repaired end to end |
| 2 Baselines | §76 | Mock, FSM, random reservoir, generic RNN available |
| 3 Functional Fly | §77 | Implemented and tested |
| 4 Environment autonomy | §78 | Real autonomous Docker/Postgres acceptance passes in bias and hard control |
| 5 Real connectome | §79 | Not accepted: existing frozen backend uses random projections, not biological topology |
| 6–14 | §80–88 | Outside this delivery; training/research/Hermes integration remain unfinished |

## Run and verify

Use Python 3.12 and install the dependencies declared in `pyproject.toml`.
The local `.venv` now includes the dependencies needed by the Phase 4 checks,
plus Ruff and mypy. Environment heuristics use `PyYAML.safe_load`.

```bash
source .venv/bin/activate
python -m pytest -q
python -m pytest tests/e2e/test_environment_autonomy.py -q
PYTHONPATH=src python -m flycode.cli run \
  --repo /tmp/flycode-environment --fixture fastapi_postgres_app \
  --task "Fix this FastAPI application and get it fully running" \
  --brain functional --control bias --max-steps 60 --max-tool-calls 60
```

The demo requires Docker/Compose and a free host port 8000. Its Postgres is
internal to Compose, so host port 5432 need not be free. Fixture destinations
are recreated. The CLI leaves the repaired service running; stop it with
`docker compose --project-directory /tmp/flycode-environment down`.
The acceptance tests choose a free HTTP port and clean up their own containers.
On this machine Colima is running and the installed Compose plugin is linked
under `~/.docker/cli-plugins/`; other projects' containers were left running.

## Phase 4 implementation

- The offline cortex reads repository files and derives narrow repairs for
  documented ports, migration paths, environment references, routes and the
  demonstrated missing SQLAlchemy import. It never reads fixture defect metadata.
  General repairs still require `--llm openai` and an API key.
- Tests and Alembic accept a Compose `service` argument, preserving the runtime
  dependencies and environment defined by the target application.
- Compose `up` rebuilds and waits for health; `ps` parses actual container states.
  Command success alone does not imply a running stack. HTTP requires a 2xx
  response and failures include the URL and status.
- Completion requires fresh passing tests and every declared infrastructure
  check: running stack, PostgreSQL, migration at head, health and API URLs.
  Edits and stack lifecycle actions invalidate verification. Premature STOP
  is rejected and cannot report verified completion.
- Successful diagnostic output reaches working memory. Edited file snapshots
  are discarded so the cortex does not repeatedly apply a stale repair.
- Infrastructure errors clear on recovery; HTTP errors are tracked per URL.
  Ordinary tool-usage failures do not poison environment health.
- Environment-variable reference fixes preserve credential values and require
  verification; actual credential edits and replacement-based bypasses remain
  blocked by the safety mask.
- The fixture retains its intended defects. Unintended invalid logging setup
  was removed, and the DB no longer publishes a host port.

## Acceptance evidence

The real `run_agent` acceptance passed for both `BIAS_CONTROL` and
`HARD_CONTROL`. Each run repairs the initially broken fixture, checks Postgres,
executes the migration, passes both application tests, checks `/health` and
`/users`, and reaches STOP. A separate SQL query verifies Alembic revision `001`.
The toy CLI smoke test also passes (`success=True`, 9 steps).

Phase 4 baseline verification (2026-10-15):

```text
python -m pytest -q -> 166 passed in 55.01s; no skips
Ruff on the new environment module, environment tools, readiness checks,
Phase 4 acceptance and associated new tests -> all checks passed
Ruff across src/tests -> 58 remaining findings
mypy src/flycode --ignore-missing-imports -> 20 errors in 8 files
```

Repository-wide static checks are not clean. Remaining findings include unused
imports, duplicate definitions, import sorting, existing model/category typing,
brain state typing and the hard/bias tournament result type. They were not
silenced or folded into an unrelated cleanup. Raw diagnostics from this session
are in `/tmp/flycode-ruff.log` and `/tmp/flycode-mypy.log`.


## Remaining work outside Phase 4

- Real connectome data, constrained training, reduced models, distillation,
  bounded online learning, causal evaluation and Hermes integration.
- CLI `train`, `distill`, `benchmark`, `lesion`, `sensitivity` remain explicit
  stubs. `--brain full` / `--brain reduced` are not supported.
- Stressors declared on TaskSpec are not wired into the loop: partial
  observations, delayed rewards, context reversal and noisy feedback.
- Benchmark coverage remains narrower than the research success criteria.
  Investigate baseline performance before making comparative capability claims.
- Pre-existing duplicate `toy_import_bug` definitions and duplicate backend test
  methods remain. No unrelated refactoring or cleanup was performed.
