Phase 4: autonomous environment acceptance

Scope: docs/Plan.md §§49–52,78 only; preserve the existing working tree.

- [x] 1. Make Docker/HTTP/migration evidence truthful and visible to the cortex.
   Files: tools/environment_tools.py, tools/tests.py, environment/software_gym.py,
   agent.py. Tests: unit/tools/test_environment_tools.py,
   unit/environment/test_software_gym.py, integration/test_agent_loop.py.
   Verify empty/stopped/unhealthy compose ps cannot mark the stack running;
   down clears readiness; HTTP errors carry diagnostics; successful diagnostics
   enter working memory; edits invalidate previously passing tests.
- [x] 2. Execute migrations and tests inside a declared Compose service.
   Files: tools/environment_tools.py, tools/tests.py, llm/prompts.py.
   Add service arguments with argument-list subprocesses, no shell strings.
   Verify service execution, migration failure/retry, build after source edits.
- [x] 3. Complete the deterministic environment cortex using observed files only.
   File: llm/environment_candidates.py, llm/candidate_generator.py.
   Read README/compose/migration/application/test files, compare documented ports,
   migration directory, environment references, routes and missing imports.
   Never read fixture defect metadata or execute a hard-coded repair transcript.
   Verification: unit/llm/test_environment_candidates.py plus real run_agent e2e.
- [x] 4. Require infrastructure checks for environment completion.
   Files: domain/observation.py, environment/software_gym.py, agent.py.
   Tests: unit/domain/test_models.py, unit/environment/test_software_gym.py.
   Require measured database, applied migration, running stack, HTTP health and
   endpoint success; toy tasks still require tests only. Reject premature STOP.
- [x] 5. Fix unintended fixture setup problems, document runnable commands and evidence.
   Files: environment/fixtures.py, tests/e2e/test_environment_autonomy.py,
   docs/quickstart.md, docs/handoff.md. The five intended defects stay broken.
   Verify actual autonomous repair for functional brain, bias and hard control,
   then run pytest and installed lint/type tools. No phase-5+ claims.

Verification: 166 tests passed, including real autonomous Docker acceptance in both control modes. See docs/handoff.md for static-analysis limitations.
