# Mandatory resolution inputs — 2026-10-17

## Problem and boundary

The previous real-Qwen case produced private answer3 after reading only input A
twice, skipping B and workspace. Its local verified-completion metric was true;
the independent registered case correctly failed before resolver review or apply.
See [the retained failure](2026-10-17-pinned-read-feedback.md).

The host now derives mandatory conflict paths and pins from the existing
canonical integration record. Completed non-late read calls must match exact
attempt/fence, arguments, selector/path/hash and workspace. The current workspace
hash is checked before edit/create admission; receipt metadata is rechecked in
the admission transaction. No inference or snapshot inspection holds that lock.

Resolution workspace reads use the existing host RPC and real bounded UTF-8
reader, at most3,500 bytes. Binary, oversized, invalid UTF-8 or absent required
files fail explicitly, without content or a successful observation. Ordinary
mutable-workspace reading outside resolution retains its previous implementation.
Denied writes have no effect/debit, return actionable English diagnostics and
publish `resolution_input_blocked` in the same event journal. Failures remain
usage errors rather than infrastructure-health errors. Failed reads enter memory
as diagnostics, never fictitious empty file text; context compaction retains them.

Publication, fresh review, integration and current-result evaluation require
these observations. Historical reviews/artifacts remain available but cannot
waive missing receipts. A previously applied result from the older policy is
invalidated without rolling back its source, changing old records or refunding
spending. This is trusted-local orchestration, not a hostile-code sandbox or proof
of the model's internal reasoning. Numeric guesses and local tests alone are
insufficient. No second queue/store or schema migration is introduced.

## TDD and targeted evidence

Corrected initial regressions reproduce both premature mutation and acceptance
of a real stopped worker's private guess (2 genuine RED failures). The first test
draft had missing imports; those logs are retained but are not bug evidence.
Independent REDs cover a legacy artifact accepted into review, an older actual
review accepted into integration, a workspace read replay raising instead of
returning failure, missing blocker events and four discarded read diagnostics.

Final targeted selection:15 exact cases,7 passed in100.91s and8 in105.70s across
two independent pytest processes. Fourteen are new cases; one is the existing
actual HTTP/process resolution through fresh review, combined/source checks and
current verified completion. The matrix includes wrong paths, stale workspace
bytes, replay, unsupported content, another attempt's receipts, budget/deadline
preservation and restoration of gates after an actually applied older pipeline.
Ruff passes; normal mypy passes47 source files including the agent and new module.

Positive fixture instructions were extended to perform the newly required
workspace read, retaining every result assertion. A first substring check
mistook a pinned fact for workspace; an anchored actual-workspace fact corrected
that fixture. Another negative fixture initially assumed README existed; it now
reads the existing non-conflicting owner-test path. All failed runs remain
retained; no assertion, required oracle, budget or timeout was weakened.

## Frozen final measurement

Implementation fingerprint:
`18048c2e84b1f3fa860e8d6802a72a89ae7db4ca5526f033f903267c583bedab`.
Archive: `~/.cache/flycode/validation/flycode-input-gate-7wpnfz0_`.
Source and README stayed frozen throughout the final measurements.

| Verification | Result |
| --- | --- |
| Full default suite,859 exact IDs once |851 passed,8 skipped;636.040s |
| Extracted wheel focused gate/ordinary-worker checks |17 passed;241.31s pytest |
| Actual FlyWire graph |5 passed;10.01s pytest |
| Relevant Ruff / normal mypy | pass /47 source files pass |
| Wheel source / README metadata |133 exact Python files / match |
| Extracted CLI |10 controllers |
| Explicit Qwen primary probe | first try passed;26.274s |

Wheel SHA256:
`bdb138b1f1c9bfeaa47ca090ce57153a227d0090e5ca5b41a2537d8ef433059d`.
The full run keeps the fixed-Compose-project module in one process and compares
collected/JUnit case Counters. The native failure below is retained separately from these passing program
checks. Source/environment identity is unchanged before/after.

The new English native protocol retains both writer objectives, sum objective,
owner oracle and original budgets:60 model calls/120 tools/30min,4 active
instances,64 tasks,3 attempts per task and20% verification reserve; authors8/12,
resolver16/24. It additionally checks a resolver workspace receipt for initial
answer0 and all52 registered checks' natural group-stop receipts. No scorer,
automatic model loading or fallback is allowed. This repeated known fixture is
an engineering regression, not a new held-out scientific cohort.

## Actual native failure and next feedback

The case FAILS in488.441s:15 actual workflow model calls/27 admitted tools.
Both authors produce1/2 and pass independent review. The resolver completes9
model calls, consumes both exact pinned versions and the initial workspace0,
then proposes `edit_file` with candidate metadata incorrectly nested in tool
arguments: `confidence`, `expected_information_gain`, `expected_progress`,
`expected_risk`, `rationale`. The strict whitelist rejects them before admission,
debit or mutation. That routine usage error escapes the environment and terminates
the worker, so there is no resolver artifact/review/integration or positive
completion. The original source remains0 and user dirty bytes are intact.

The2228-byte complete retained stderr identifies
`permission: Unsupported tool arguments`; this is not a timeout/context-budget
claim. All16 model records (15 workflow requests plus1 probe) completed with
known token usage and no provider errors. The final provider response is uniquely
correlated by start timestamp and exact SDK usage; normalized tool arguments and
response hash are retained, excluding request/header contents. There are no
scorer requests, automatic loading or fallback.

All3 worker groups are absent; all8 executed registered checks have natural stop
receipts. No resolver verification was executed. Spending is known, reservations
zero. Explicit owner cancellation closes the failed workflow without changing
any attempt, artifact or spending; no stop acknowledgment or budget reset is
needed. Independent verification checks exact case Counters, all133 wheel files,
README metadata, physical custody receipts and unchanged historical checkpoints.

The next RED should keep the strict whitelist and give the agent recoverable
feedback for malformed tool arguments, while fence/deadline/budget/cancellation
and actual authority violations remain terminal. Do not silently strip metadata
or broaden permissions. This is new engineering feedback, not proof of neural
benefit, successful resolution or a reason to promote a trained checkpoint.

The historical296 English runs,306 independent audits and10 trained checkpoints
remain separate and unchanged. Both trained variants are still rejected; defaults
and biological topology remain intact. Aggregate Tasks5–8 and orchestration /
scientific admission remain open.
