"""Unit tests for the deterministic heuristic LLM cortex stand-in."""

from flycode.domain import Observation
from flycode.llm import CortexContext, HeuristicCandidateGenerator

PYTEST_ARITHMETIC_STDOUT = """F.
=================================== FAILURES ===================================
___________________________________ test_add ___________________________________

    def test_add():
>       assert add(2, 3) == 5
E       assert 6 == 5

tests/test_mathutils.py:5: AssertionError
1 failed, 1 passed in 0.01s
"""

MATHUTILS_SOURCE = (
    '"""Small arithmetic helpers."""\n\n\n'
    "def add(a, b):\n"
    "    return a + b + 1  # BUG: extra off-by-one term\n\n\n"
    "def subtract(a, b):\n"
    "    return a - b\n"
)


def observation(mode="explore", file_tree=None, failures=None):
    return Observation(
        task="fix add()",
        behavior_mode=mode,
        file_tree=file_tree or ["mathutils.py", "tests/test_mathutils.py"],
        tests={"status": "failed", "failed": 1, "failures": failures or []},
    )


class TestExploreDiagnose:
    def test_explore_proposes_a_source_read(self):
        gen = HeuristicCandidateGenerator()
        ctx = CortexContext(task="fix add()", behavior_mode="explore", observation=observation())
        tools = [c.tool for c in gen.generate_candidates(ctx)]
        assert "read_file" in tools
        assert "list_files" in tools

    def test_diagnose_proposes_run_tests_first(self):
        gen = HeuristicCandidateGenerator()
        ctx = CortexContext(task="fix add()", behavior_mode="diagnose", observation=observation())
        candidates = gen.generate_candidates(ctx)
        assert candidates[0].tool == "run_tests"


class TestExecuteRepair:
    def test_execute_without_pytest_output_falls_back(self):
        gen = HeuristicCandidateGenerator()
        ctx = CortexContext(task="fix add()", behavior_mode="execute", observation=observation(), working_memory=[])
        candidates = gen.generate_candidates(ctx)
        assert candidates  # falls back to diagnose-style candidates
        assert candidates[0].tool == "run_tests"

    def test_execute_requests_missing_source_before_fixing(self):
        gen = HeuristicCandidateGenerator()
        wm = [f"run_tests stdout:\n{PYTEST_ARITHMETIC_STDOUT}"]
        ctx = CortexContext(task="fix add()", behavior_mode="execute", observation=observation(), working_memory=wm)
        candidates = gen.generate_candidates(ctx)
        assert len(candidates) == 1
        assert candidates[0].tool == "read_file"
        assert candidates[0].arguments["path"] == "mathutils.py"

    def test_execute_produces_correct_arithmetic_fix_once_source_known(self):
        gen = HeuristicCandidateGenerator()
        wm = [
            f"run_tests stdout:\n{PYTEST_ARITHMETIC_STDOUT}",
            f"read_file(mathutils.py):\n{MATHUTILS_SOURCE}",
        ]
        ctx = CortexContext(task="fix add()", behavior_mode="execute", observation=observation(), working_memory=wm)
        candidates = gen.generate_candidates(ctx)
        assert len(candidates) == 1
        fix = candidates[0]
        assert fix.tool == "edit_file"
        assert fix.arguments["path"] == "mathutils.py"
        assert fix.arguments["old_text"] == "    return a + b + 1  # BUG: extra off-by-one term"
        assert fix.arguments["new_text"] == "    return a + b  # BUG: extra off-by-one term"


class TestConsolidate:
    def test_consolidate_reports_outcome(self):
        gen = HeuristicCandidateGenerator()
        healthy = Observation(task="fix add()", tests={"status": "passed"})
        ctx = CortexContext(task="fix add()", behavior_mode="consolidate", observation=healthy)
        summary = gen.consolidate(ctx, transcript=["did a thing"])
        assert "succeeded" in summary
