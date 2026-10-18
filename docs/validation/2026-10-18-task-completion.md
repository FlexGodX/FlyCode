# Checked task completion, 2026-10-18

Schema22 adds an immutable TaskCompletion receipt to the canonical Store.
`Completions.complete` requires a stopped author, exact independently approved
review, current criteria/oracle/environment, settled spending and pinned
prerequisites. It atomically marks the task succeeded and unlocks declared
successor eligibility. Owner source remains unchanged; workflow integration
still requires its own completion gate. `result_id` retains its original meaning.

Source/evidence/contract changes and cancellation revoke receipts and dependent
ownership. Active consumers keep their reservation until exact stop settlement;
a real worker's late HTTP reply is charged and cannot publish a patch. Review
dependencies work before their authors finish, and reopened reviewers use the
current successful attempt. Metadata/archive preserve evidence. File reads reject
FIFOs, links and oversized records; a bounded physical evidence inventory is
rechecked after prerequisite preflight. No project filesystem work runs in SQL.

## Measurements and boundaries

- Initial new gate/readers:40pass/166.62s. Migration composition:8pass/2.41s.
- Directly affected Store/workspace/review/integration/v1/v2 cases:226pass,
  4fail/1277.46s on the earlier frozen source. All four were diagnosed and
  individually reverified; this is not a clean initial run or a repeat of all230.
- Corrected source:16pass/225.61s, including legacy resolved-root reopen and
  dependent revocation. Cancel/pause2pass/50.45s; real coordinator crash2pass/
  20.12s; actual dependent worker Stop1pass/5.26s; foreign/read-only scope3pass/
  11.09s. These24 cases overlap the earlier measurements.
- Earlier wheel:12pass/85.17s. Final wheel:7pass/123.15s plus3pass/26.41s,
  including real crashed coordinators and late descendant HTTP.170 package files
  and UTF-8 README exactly match the isolated source. Extracted CLI help passes.
- Ruff19 files, scoped mypy7 source files and diff checks pass. Two isolated
  snapshots retain183/184 recorded inputs unchanged; publication inputs match
  the corrected snapshot. Parallel CLI/MaleCNS/brain changes are excluded.

The initial legacy reopen failure exposed generic invalidation overwriting
`result_invalidated` review markers. The correction preserves these markers and
runs legacy invalidation first. The cancellation expectation now checks actual
fence revocation and retained reservation rather than the former later policy
error. Two old crash harnesses imported the editable checkout instead of the
isolated package; they now derive child PYTHONPATH from the imported module.

One new-case setup was inconclusive while the parallel brain implementation
changed its package fingerprint. It was repeated from a fixed isolated copy;
the actual manifest-FIFO RED was then reproduced. A later wheel crash harness
also duplicated its metadata path: parent saw3 FlyCode distribution entries,
child2, with identical implementation bytes. Removing the duplicate harness
path restored matched environments; both crash cases pass. All failures, the
initial missing-helper REDs and the earlier test setup error remain retained.

## Evidence and remaining scope

Raw records, logs, wheel, exact input hashes and7288 captured regular evidence
files are saved privately in
`~/.cache/flycode/validation/flycode-task-completion-tqcak40m`.
Archive manifest SHA256:
`ff7fc5b56df78308b4b0cc28386b393a149b2a243baac8b6c9153227dee6eefe`.
Source manifest SHA256:
`1233ad16560f02fd53f39d032378d0c35f915b93183ffa10122b659811cc6111`.
The adjacent JSON records exact timings and wheel hash. Source/model fixtures
and real subprocesses were used; no live-model, graph, training, browser or full
repository measurement is claimed. Independent read-only review found no
remaining Task1 blocker after correction.

Task2 already has a real failing checkout test: A succeeds with immutable x1,
but the admitted successor still receives original x0. Composing pinned outputs,
sequential final integration, general recipes, child wait/replan and shared result
views remain open in the dependent-execution plan. Overall Tasks5–8 and research
§§90–99 are not closed. This internal gate does not add a public execution route.
