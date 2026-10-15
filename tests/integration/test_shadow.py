"""Shadow recommendations cannot change active-controller actions or routing."""

import numpy as np

from flycode.agent import run_agent
from flycode.brain import make_brain
from flycode.brain.full_connectome.graph import graph_from_edges
from flycode.domain import TaskSpec
from flycode.environment import SoftwareEnvironment
from flycode.llm import HeuristicCandidateGenerator


async def test_shadow_keeps_executed_actions_identical_and_reports_comparison(tmp_path):
    from flycode.brain.shadow import ShadowBackend

    path = tmp_path / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(path)
    task = TaskSpec(id='shadow', task='fix', fixture='toy_arithmetic_bug')
    baseline = await run_agent(SoftwareEnvironment(tmp_path / 'base'), make_brain('functional', seed=0),
                               HeuristicCandidateGenerator(), task)
    brain = ShadowBackend(make_brain('functional', seed=0), make_brain('full', connectome_path=path, seed=0))
    events = []
    shadow = await run_agent(SoftwareEnvironment(tmp_path / 'shadow'), brain, HeuristicCandidateGenerator(), task,
                            on_event=lambda kind, data: events.append((kind, data)))
    assert shadow.success
    assert shadow.transcript == baseline.transcript
    assert shadow.metrics.llm_calls == baseline.metrics.llm_calls
    reports = [data for kind, data in events if kind == 'shadow']
    assert len(reports) == shadow.steps
    assert all(report['influence'] == 0. for report in reports)
    assert all(report['counterfactual_success'] is None for report in reports)
    assert all(report['observer_latency_ms'] > 0 for report in reports)
    assert shadow.metrics.shadow_decisions == shadow.steps
    assert not brain.observer.learning_enabled()


async def test_shadow_runtime_failure_does_not_stop_the_active_controller(tmp_path):
    from flycode.brain.mock import MockBrainBackend
    from flycode.brain.shadow import ShadowBackend

    class FailingObserver(MockBrainBackend):
        def step(self, stimulus):
            raise ValueError('deliberate observer failure')

    brain = ShadowBackend(make_brain('functional', seed=0), FailingObserver())
    events = []
    result = await run_agent(SoftwareEnvironment(tmp_path), brain, HeuristicCandidateGenerator(),
        TaskSpec(id='observer-error', task='fix', fixture='toy_arithmetic_bug'),
        on_event=lambda kind, data: events.append((kind, data)))
    assert result.success
    assert any(data['error'] == 'deliberate observer failure' for kind, data in events if kind == 'shadow')


def test_shadow_reset_clears_state_and_closes_failed_observer():
    from flycode.brain import make_stimulus
    from flycode.brain.mock import MockBrainBackend
    from flycode.brain.shadow import ShadowBackend
    from flycode.domain import Observation

    class FailingObserver(MockBrainBackend):
        closed = False

        def step(self, stimulus):
            raise ValueError('failed')

        def close(self):
            self.closed = True

    observer = FailingObserver()
    brain = ShadowBackend(make_brain('functional', seed=0), observer)
    brain.step(make_stimulus(Observation(task='reset')))
    assert brain.describe()['steps'] == 1
    brain.close()
    assert observer.closed
    brain.reset()
    assert brain.get_policy_bias() == brain.active.get_policy_bias()
    assert brain.describe()['steps'] == 0
