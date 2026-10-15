# LM Studio dual-model implementation plan

**Goal:** connect the installed Qwen3.5 9B and Jev-Style Qwen3.5 2B models to FlyCode, with the brain controlling inference calls and retaining final action arbitration.

**Architecture:** Qwen proposes concrete actions; Jev scores the safe options with its native single-token decision format. A deterministic routing policy reads the current neural state and observation before inference. Existing bias/hard tournaments and safety checks remain authoritative. The current Functional Fly is not a biological connectome; the integration accepts the shared NeuralState contract for future backends.

**Spec:** `docs/Plan.md` §§2–5,23–30,49–50,66 and the user's local two-model integration request.

**Tech stack:** Python 3.12, Pydantic, existing OpenAI-compatible client, httpx, LM Studio at `http://127.0.0.1:1234/v1`.

## Constraints

- Use the actual installed IDs: `qwen/qwen3.5-9b` and `jev-style-qwen3.5-2b-decision`.
- Jev v1 is a choice scorer, not a chat planner. Send `[State]`, `[Question]`, `[Options]`, `Answer:` and request one token plus top-20 log probabilities.
- No cloud fallback, no model downloads, no changes to model weights or real-connectome claims.
- Keep default heuristic and existing OpenAI behavior compatible.
- Preserve final safety masking and per-candidate IDs. Decision probabilities cannot authorize unsafe tools.
- Bound inference with explicit timeouts, token limits, zero automatic retries and a per-run model-call budget. Read optional authentication from environment only.
- Implement natively in this session as explicitly requested, with failing tests before production changes.

## Review focus

1. A response can be valid JSON but have the wrong shape, nonfinite scores, unknown tools or invalid candidate fields; reject these safely.
2. A model can be installed but not available under the requested API ID; report exact missing IDs without guessing or using a remote provider.
3. Jev may omit option letters from top_logprobs or return no usable log probabilities; reject unusable distributions and retain safe Qwen proposals with a visible diagnostic.
4. A confident or blocked controller, verified completion or deterministic verification should skip inference; count actual requests, including failed requests, without hidden consolidation calls.
5. Jev failure, Qwen failure and exhausted budgets must leave a truthful, inspectable result; neither model can bypass safety or verified STOP.

## Task 1 — neural routing contract

Files: `src/flycode/llm/protocol.py`, new `src/flycode/policy/model_routing.py`, new `tests/unit/policy/test_model_routing.py`.

Interfaces: add optional `neural_state: NeuralState | None` and `control_mode` to CortexContext. Produce a Pydantic ModelCallPlan describing whether the reasoner/decider should run and an optional local candidate.

- [x] Write table-driven tests: blocked brain skips both; healthy observation stops locally; VERIFY on an unverified non-Compose repo runs tests locally; semantic work calls Qwen; high confidence and hard control skip Jev while uncertain bias control requests it.
- [x] Run the tests and observe missing/incorrect behavior.
- [x] Implement the minimum routing policy using existing neural confidence, mode and block signals.
- [x] Verify all routing cases and existing cortex tests.

## Task 2 — LM Studio model clients

Files: `src/flycode/llm/openai_compatible.py`, new `src/flycode/llm/decision.py`, new `tests/unit/llm/test_local_models.py`.

Interfaces: OpenAICompatibleLLM gains optional bounded/structured local requests. JevStyleDecisionClient.score(context, candidates) returns probabilities keyed by candidate ID, never tool name.

- [x] Use a local HTTP test server to capture real client requests and return complete API responses.
- [x] Test structured candidate generation, invalid JSON shapes/values, timeouts, wrong model IDs and metadata preflight.
- [x] Test Jev prompt suffix, one-token request, finite normalized probabilities, correct ID mapping, missing/duplicate option token handling and unavailable log probabilities.
- [x] Implement clients and verify the tests. Local requests must not depend on OPENAI_API_KEY or inherit proxy routing.

## Task 3 — routed cortex and execution evidence

Files: new `src/flycode/llm/lmstudio.py`, `src/flycode/llm/__init__.py`, `src/flycode/agent.py`, `src/flycode/policy/bias_control.py`, `src/flycode/telemetry/schema.py`, new `tests/integration/test_local_cortex.py`, `tests/unit/policy/test_policy.py`.

Interfaces: LMStudioCortex implements LLMCortex, exposes per-model request counts and a Pydantic last-routing trace. Candidate rank_score carries optional Jev evidence in bias control; hard control remains brain-only. RunMetrics adds per-model counts; TrainingStep adds the routing trace.

- [x] Test that skipped models receive no inference request; changing neural confidence changes Jev invocation.
- [x] Test safe proposals survive a decider outage and that reasoner failure stops visibly without executing invented actions.
- [x] Test exact budget exhaustion, no extra consolidation inference, per-run counter reset and bounded candidate count.
- [x] Test malicious/unsafe proposals stay blocked regardless of Jev scores.
- [x] Test brain scoring can override Jev preference and hard control keeps its semantics.
- [x] Run a full toy repair through real HTTP clients and mocked HTTP responses, retaining real tools, policy and environment.
- [x] Implement the integration and verify its tests plus existing agent tests.

## Task 4 — CLI, documentation and live acceptance

Files: `src/flycode/cli.py`, new `tests/unit/test_cli.py`, new opt-in `tests/e2e/test_lmstudio.py`, `README.md`, `README.en.md`, `docs/quickstart.md`, `docs/handoff.md`.

- [x] Test `--llm lmstudio`, `--base-url`, `--model`, `--decision-model`, positive request timeout and model-call budget; existing commands keep defaults.
- [x] Document exact model IDs, LM Studio loading/server commands, architecture, controlled skips, failure behavior and the lack of a real connectome.
- [x] Run both installed models locally: Qwen returns valid candidates and Jev returns finite option probabilities.
- [x] Run a real toy repair with `--llm lmstudio --brain functional`; inspect model calls and routing telemetry.
- [x] Run the full pytest suite, targeted Ruff and mypy; report existing repository-wide static-check debt separately.
- [x] Record observed results and update both language versions consistently.

## Primary references

- Installed model card: https://huggingface.co/chaoliangUNSW/Jev-Style-Qwen3.5-2B-Decision-GGUF
- LM Studio structured output: https://lmstudio.ai/docs/developer/openai-compat/structured-output
- Native Jev v1 client: https://huggingface.co/chaoliangUNSW/Jev-Style-Qwen3.5-2B-Decision-GGUF/blob/main/jev_style_client.py

## Execution results

- Main suite: 206 passed, 1 opt-in live test skipped (89.52 seconds).
- Explicit LM Studio acceptance: 1 passed (148.09 seconds).
- Real CLI acceptance: successful repair in 6 steps, 3 Qwen calls and 3 Jev
  calls, no request errors. Deterministic checks and STOP skipped inference.
- Targeted Ruff passed. Repository-wide debt remains 57 Ruff findings and the
  existing 20 mypy errors in 8 files; no new mypy errors were introduced.
- The initial 60-second request cap was too short for this machine's measured
  Qwen throughput. The configurable local default is 120 seconds, validated by
  both the successful CLI run and the independent live acceptance test.
