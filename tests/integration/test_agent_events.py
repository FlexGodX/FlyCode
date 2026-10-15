"""Live progress is observable before actions execute, and stop is cooperative."""

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.domain import CandidateAction, TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator


async def test_events_explain_real_repair_in_execution_order(tmp_path):
    events = []
    result = await run_agent(
        SoftwareEnvironment(tmp_path / "repo"), make_brain("functional"), HeuristicCandidateGenerator(),
        TaskSpec(id="events", task="fix arithmetic", fixture="toy_arithmetic_bug"),
        on_event=lambda kind, data: events.append((kind, data)),
    )
    assert result.success
    kinds = [kind for kind, _ in events]
    assert kinds.index("controller") < kinds.index("selection") < kinds.index("tool_start") < kinds.index("tool_end")
    selections = [data for kind, data in events if kind == "selection"]
    assert any(data["chosen_action"]["tool"] == "edit_file" for data in selections)
    assert all(data["chosen_action"]["rationale"] for data in selections)
    outcomes = [data for kind, data in events if kind == "tool_end"]
    assert any(data["observation"]["tests"]["status"] == "passed" for data in outcomes)


async def test_stop_during_generation_does_not_execute_proposed_edit(tmp_path):
    stopped = False

    class StoppingCortex(HeuristicCandidateGenerator):
        def generate_candidates(self, context):
            nonlocal stopped
            stopped = True
            return [CandidateAction(id="edit-after-stop", tool="create_file", arguments={"path": "unexpected.py", "content": "bad"})]

    result = await run_agent(
        SoftwareEnvironment(tmp_path / "repo"), make_brain("functional"), StoppingCortex(),
        TaskSpec(id="stop", task="fix", fixture="toy_arithmetic_bug"),
        should_stop=lambda: stopped,
    )
    assert result.tool_calls == 0
    assert not (tmp_path / "repo" / "unexpected.py").exists()
