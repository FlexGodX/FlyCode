"""Heuristic candidate generator - deterministic stand-in for the LLM cortex.

Used as the default "teacher" for offline demos, unit tests and CI (plan
§54: "high-quality LLM agent" / "heuristic controller" as imitation
teachers) so the full agent loop is exercised without a network call or an
API key. `openai_compatible.py` implements the same `LLMCortex` protocol
against a real model for production use.

The repair heuristics here are intentionally narrow (an extra/missing
additive constant, a missing top-level import) but operate generically on
whatever pytest reports - they read the traceback's file:line and the
`assert actual == expected` mismatch rather than any fixture-specific text.
"""

import re

from ..domain import DEFAULT_CANDIDATE_COUNT, CandidateAction
from .environment_candidates import environment_candidates
from .protocol import CortexContext

_CALL_RE = re.compile(r"^>\s+assert\s+(?P<func>\w+)\(")
_ASSERT_RE = re.compile(r"^E\s+assert\s+(?P<actual>-?\d+(?:\.\d+)?)\s*==\s*(?P<expected>-?\d+(?:\.\d+)?)")
_SUMMARY_FRAME_RE = re.compile(r"^(?P<file>\S+\.py):(?P<line>\d+):\s*(?P<exc>\w+)$")
_NAME_ERROR_RE = re.compile(r"^E\s+NameError: name '(?P<name>\w+)' is not defined")
_READ_RE = re.compile(r"^read_file\((?P<path>[^)]+)\):\n(?P<content>.*)$", re.DOTALL)
_PYTEST_RE = re.compile(r"^run_tests stdout:\n(?P<stdout>.*)$", re.DOTALL)

# Symbols the missing-import heuristic knows how to resolve.
KNOWN_MODULES = {"datetime", "os", "sys", "json", "re", "math", "time", "logging"}

# Compose file names that mark a repository as declaring its own service stack.
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def _infra_candidates(obs) -> list[CandidateAction]:
    """Propose bringing the repository's declared stack up (plan §78).

    Deliberately shallow: the MVP tools cannot discover a service topology on
    their own, so the rule only fires when the repo declares a compose file and
    Docker does not already report the stack as running. Which service is
    broken, which migration is missing and which URL to probe stay the LLM
    cortex's job.
    """
    if not any(name in obs.file_tree for name in COMPOSE_FILES):
        return []
    if obs.docker.get("running") is True:
        return []
    if obs.docker.get("last_action") == "up":
        # An `up` was already issued and the stack still is not running: stop
        # re-issuing the same call and let diagnosis/recovery take over.
        return []
    return [
        _candidate(
            "docker_compose",
            {"action": "up"},
            "bring the declared compose stack up before reasoning about the code",
            expected_progress=0.7,
            expected_information_gain=0.5,
        ),
        _candidate(
            "postgres_status",
            {},
            "check whether the database service became reachable",
            expected_progress=0.2,
        ),
    ]


class HeuristicCandidateGenerator:
    """Deterministic rule-based `LLMCortex` implementation."""

    def generate_candidates(self, context: CortexContext) -> list[CandidateAction]:
        mode = context.behavior_mode
        obs = context.observation
        file_contents = _extract_read_files(context.working_memory)
        pytest_stdout = _extract_pytest_stdout(context.working_memory)

        environment = environment_candidates(
            obs, file_contents, _candidate, lambda: self._execute(obs, file_contents, pytest_stdout)
        )
        if environment:
            return environment

        # Reflex (plan §78): while a declared service stack is down, every
        # DB/HTTP-backed tool call fails for infrastructure reasons, so bringing
        # the stack up has to happen before anything is scored - the same reason
        # the recovery reflex bypasses the LLM (plan §31). Once the stack is up,
        # or once an `up` has already failed, normal scoring resumes.
        infra = _infra_candidates(obs)
        if infra:
            return infra

        if mode == "explore":
            return self._explore(obs)
        if mode in ("diagnose", "plan"):
            return self._diagnose(obs)
        if mode == "execute":
            return self._execute(obs, file_contents, pytest_stdout) or self._diagnose(obs)
        if mode in ("verify", "recover"):
            return self._verify(obs)
        return [_candidate("stop", {}, "task appears complete or no further action is heuristically known")]

    def consolidate(self, context: CortexContext, transcript: list[str]) -> str:
        obs = context.observation
        outcome = "succeeded" if obs.is_healthy else "did not fully succeed"
        return (
            f"Task '{context.task}' {outcome} after {obs.step_number} steps. "
            f"Final test status: {obs.tests_status}. Steps taken: {len(transcript)}."
        )

    # ------------------------------------------------------------ per-mode rules
    def _explore(self, obs) -> list[CandidateAction]:
        candidates = [
            _candidate("list_files", {}, "survey the repository layout"),
            _candidate("search", {"pattern": "BUG"}, "look for marked defects"),
            _candidate("git_status", {}, "check for pre-existing local changes"),
        ]
        source = _guess_source_file(obs.file_tree)
        if source:
            candidates.append(_candidate("read_file", {"path": source}, f"inspect the main module {source}"))
        return candidates[:DEFAULT_CANDIDATE_COUNT]

    def _diagnose(self, obs) -> list[CandidateAction]:
        candidates = [_candidate("run_tests", {}, "get an authoritative pass/fail signal")]
        failures = obs.tests.get("failures") or []
        if failures:
            test_ref = failures[0].get("test", "")
            test_file = test_ref.split("::")[0]
            if test_file:
                candidates.append(_candidate("read_file", {"path": test_file}, "read the failing test"))
        source = _guess_source_file(obs.file_tree)
        if source:
            candidates.append(_candidate("read_file", {"path": source}, "read the implementation under test"))
        return candidates[:DEFAULT_CANDIDATE_COUNT]

    def _execute(self, obs, file_contents: dict[str, str], pytest_stdout: str | None) -> list[CandidateAction]:
        if not pytest_stdout:
            return []

        arithmetic = _parse_arithmetic_failure(pytest_stdout)
        if arithmetic:
            for path, content in file_contents.items():
                fix = _repair_arithmetic(content, arithmetic)
                if fix:
                    old_line, new_line = fix
                    diff = abs(int(arithmetic["actual"] - arithmetic["expected"]))
                    return [
                        _candidate(
                            "edit_file",
                            {"path": path, "old_text": old_line, "new_text": new_line},
                            f"correct the off-by-{diff} arithmetic error in {arithmetic['func']}",
                            expected_progress=0.9,
                            confidence=0.85,
                        )
                    ]
            # The fix needs source we have not read yet - go get exactly that,
            # rather than falling back to a generic diagnose set (which would
            # just re-run the already-known-failing tests).
            source = _guess_source_file(obs.file_tree)
            if source and source not in file_contents:
                return [
                    _candidate(
                        "read_file",
                        {"path": source},
                        "need the implementation source to compute the arithmetic fix",
                        expected_progress=0.6,
                    )
                ]
            return []

        missing_import = _parse_missing_import(pytest_stdout)
        if missing_import:
            if missing_import["file"] not in file_contents:
                return [
                    _candidate(
                        "read_file",
                        {"path": missing_import["file"]},
                        "need the file contents before inserting the import",
                        expected_progress=0.6,
                    )
                ]
            if missing_import["name"] in KNOWN_MODULES or missing_import["name"] == "select":
                content = file_contents[missing_import["file"]]
                first_line = content.splitlines()[0] if content else ""
                statement = ("from sqlalchemy import select" if missing_import["name"] == "select"
                             else f"import {missing_import['name']}")
                return [
                    _candidate(
                        "edit_file",
                        {
                            "path": missing_import["file"],
                            "old_text": first_line,
                            "new_text": f"{statement}\n{first_line}",
                        },
                        f"add missing 'import {missing_import['name']}'",
                        expected_progress=0.9,
                        confidence=0.85,
                    )
                ]
        return []

    def _verify(self, obs) -> list[CandidateAction]:
        return [
            _candidate("run_tests", {}, "confirm the fix"),
            _candidate("git_diff", {}, "review what changed"),
        ]


def _candidate(tool: str, arguments: dict, rationale: str, **overrides) -> CandidateAction:
    defaults = dict(expected_progress=0.5, expected_information_gain=0.4, expected_risk=0.05, confidence=0.6)
    defaults.update(overrides)
    return CandidateAction(id=f"{tool}-{abs(hash((tool, tuple(sorted(arguments.items()))))) % 100000}",
                            tool=tool, arguments=arguments, rationale=rationale, source="llm", **defaults)


def _guess_source_file(file_tree: list[str]) -> str | None:
    for f in file_tree:
        if f.endswith(".py") and "/" not in f and not f.startswith("test_"):
            return f
    return None


def _extract_read_files(working_memory: list[str]) -> dict[str, str]:
    contents: dict[str, str] = {}
    for entry in working_memory:
        match = _READ_RE.match(entry)
        if match:
            contents[match.group("path")] = match.group("content")
    return contents


def _extract_pytest_stdout(working_memory: list[str]) -> str | None:
    for entry in reversed(working_memory):
        match = _PYTEST_RE.match(entry)
        if match:
            return match.group("stdout")
    return None


def _parse_arithmetic_failure(stdout: str) -> dict | None:
    """Find `assert func(...) == expected` / `E assert actual == expected` pairs.

    pytest's short-traceback style does not show an intermediate frame for a
    function that merely returns a wrong value (no exception is raised inside
    it), so the fix locates the function by *name* rather than by file:line.
    """
    func: str | None = None
    result = None
    for line in stdout.splitlines():
        call = _CALL_RE.match(line.strip())
        if call:
            func = call.group("func")
        assertion = _ASSERT_RE.match(line.strip())
        if assertion and func:
            result = {
                "func": func,
                "actual": float(assertion.group("actual")),
                "expected": float(assertion.group("expected")),
            }
            func = None
    return result


def _parse_missing_import(stdout: str) -> dict | None:
    """Find a NameError and the source file:line pytest attributes it to.

    Unlike the arithmetic case, a NameError genuinely raises inside the
    buggy function, so pytest's trailing `file:line: NameError` summary line
    does point at the source file.
    """
    name: str | None = None
    for line in stdout.splitlines():
        name_error = _NAME_ERROR_RE.match(line.strip())
        if name_error:
            name = name_error.group("name")
        frame = _SUMMARY_FRAME_RE.match(line.strip())
        if frame and name and frame.group("exc") == "NameError" and "test" not in frame.group("file"):
            return {"file": frame.group("file"), "line": int(frame.group("line")), "name": name}
    return None


def _find_function_line(content: str, func: str) -> int | None:
    """Index of the first `return` line inside `def func(...)`."""
    lines = content.splitlines()
    in_func = False
    for i, line in enumerate(lines):
        if re.match(rf"def\s+{re.escape(func)}\s*\(", line.strip()):
            in_func = True
            continue
        if in_func:
            if line.strip().startswith("def "):
                break
            if "return" in line:
                return i
    return None


def _repair_arithmetic(content: str, failure: dict) -> tuple[str, str] | None:
    idx = _find_function_line(content, failure["func"])
    if idx is None:
        return None
    lines = content.splitlines()
    line = lines[idx]
    diff = int(round(failure["actual"] - failure["expected"]))
    if diff == 0:
        return None

    if diff > 0:
        pattern = re.compile(rf"\s*\+\s*{diff}\b")
    else:
        pattern = re.compile(rf"\s*-\s*{-diff}\b")

    new_line, count = pattern.subn("", line, count=1)
    if count == 0:
        return None
    return line, new_line
