"""Actual agent transitions, including STOP, feed bounded persistent credit."""

import numpy as np

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator


async def test_executed_actions_are_credited_once_including_terminal_action(tmp_path):
    brain = make_brain('functional', seed=0)
    brain.set_learning_mode(True)
    events = []
    result = await run_agent(SoftwareEnvironment(tmp_path / 'repo'), brain, HeuristicCandidateGenerator(),
        TaskSpec(id='learning', task='fix', fixture='toy_arithmetic_bug'),
        on_event=lambda kind, data: events.append((kind, data)))
    assert result.success, result.transcript
    assert brain.adaptation.state.accepted_updates + brain.adaptation.state.rollbacks == result.tool_calls
    assert len([data for kind, data in events if kind == 'learning']) == result.tool_calls
    assert np.any(brain.mushroom_body.credit['stop'])
    assert result.metrics.online_updates > 0
    assert result.metrics.online_learning_ms > 0
    learned = brain.snapshot_policy()
    brain.set_learning_mode(False)
    second = await run_agent(SoftwareEnvironment(tmp_path / 'other'), brain, HeuristicCandidateGenerator(),
        TaskSpec(id='frozen', task='fix', fixture='toy_import_bug'))
    assert second.success
    assert second.metrics.online_updates == 0
    assert brain.snapshot_policy() == learned
