"""Guarded autonomy feeds its actual control mode into the cortex after rollback."""

from flycode.agent import run_agent
from flycode.brain.functional_fly import FunctionalFlyBackend
from flycode.domain import CandidateAction, TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator
from flycode.policy import ControlMode
from flycode.telemetry import TelemetryLogger


async def test_failed_autonomous_edit_falls_back_and_finishes_with_verified_repair(tmp_path):
    class FirstEditPreference(FunctionalFlyBackend):
        def score_candidates(self, candidates, mode=None):
            if self._steps == 1:
                return {candidate.id: .8 if candidate.tool == 'edit_file' else 0. for candidate in candidates}
            return super().score_candidates(candidates, mode)

    class Cortex(HeuristicCandidateGenerator):
        def __init__(self):
            self.controls = []

        def generate_candidates(self, context):
            self.controls.append(context.control_mode)
            if context.observation.step_number == 0:
                return [CandidateAction(id='safe-read', tool='read_file', arguments={'path': 'README.md'},
                                        rank_score=.95, expected_risk=0.),
                        CandidateAction(id='bad-edit', tool='edit_file', arguments={'path': 'mathutils.py',
                            'old_text': 'missing text', 'new_text': 'replacement'}, rank_score=.05, expected_risk=.1)]
            return super().generate_candidates(context)

    cortex = Cortex()
    telemetry = TelemetryLogger(tmp_path / 'telemetry')
    events = []
    result = await run_agent(SoftwareEnvironment(tmp_path / 'repo'), FirstEditPreference(seed=0), cortex,
        TaskSpec(id='autonomy', task='fix', fixture='toy_arithmetic_bug'), control_mode=ControlMode.HARD_CONTROL,
        autonomous_guard=True, telemetry=telemetry, on_event=lambda kind, data: events.append((kind, data)))
    assert result.success, result.transcript
    assert result.metrics.control_mode == 'hard_control'
    assert result.metrics.autonomy_rollbacks == 1
    assert cortex.controls[0] == 'hard_control'
    assert all(mode == 'bias_control' for mode in cortex.controls[1:])
    steps = telemetry.read_steps('run')
    assert steps[0].chosen_action['id'] == 'bad-edit'
    assert steps[0].autonomy_decision['rolled_back']
    assert steps[1].autonomy_decision['applied_control'] == 'bias_control'
    assert any(kind == 'autonomy_rollback' for kind, data in events)
