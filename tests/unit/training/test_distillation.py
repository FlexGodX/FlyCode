"""Selected teacher tool biases are distilled and evaluated by whole episode."""

import json

import numpy as np
import pytest

from flycode.brain import make_brain, make_stimulus
from flycode.brain.full_connectome.graph import graph_from_edges
from flycode.domain import Observation
from flycode.telemetry import TrainingStep
from flycode.training.constrained import train_connectome


def teacher_and_episodes(tmp_path):
    path = tmp_path / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(path)
    episode = [TrainingStep(observation=Observation(task='fix', step_number=i,
                            tests={'status': 'passed' if i == 2 else 'failed', 'failed': int(i < 2)},
                            modified_files=['app.py'] if i else []).model_dump(),
                            chosen_action={'tool': tool}, outcome={'success': True})
               for i, tool in enumerate(['read_file', 'edit_file', 'stop'])]
    episodes = [episode, episode, episode, episode]
    train_connectome(path, episodes[:2], tmp_path / 'teacher')
    return path, episodes


def test_distilled_student_reproduces_heldout_teacher_biases(tmp_path):
    from flycode.training.distillation import distill_controller

    graph, episodes = teacher_and_episodes(tmp_path)
    output = tmp_path / 'student'
    report = distill_controller(graph, tmp_path / 'teacher', episodes, output,
                               max_rmse=.05, min_agreement=1.)
    assert report['accepted']
    assert report['train_episodes'] == 3
    assert report['validation_episodes'] == 1
    assert report['validation']['rmse'] < .05
    student = make_brain('functional', checkpoint_path=output)
    assert student.parameter_count() == make_brain('functional').parameter_count() + 784
    teacher = make_brain('full', connectome_path=graph, checkpoint_path=tmp_path / 'teacher')
    for step in episodes[-1]:
        stimulus = make_stimulus(Observation.model_validate(step.observation))
        expected = teacher.step(stimulus).action_bias
        actual = student.step(stimulus).action_bias
        assert max(actual, key=actual.get) == max(expected, key=expected.get)
    student.reset()
    assert student._distilled_readout is not None
    json.dumps(report, allow_nan=False)


def test_distillation_requires_separate_validation_episodes_and_trained_teacher(tmp_path):
    from flycode.training.distillation import distill_controller

    graph, episodes = teacher_and_episodes(tmp_path)
    with pytest.raises(ValueError, match='episode'):
        distill_controller(graph, tmp_path / 'teacher', episodes[:1], tmp_path / 'student')
    untrained = make_brain('full', connectome_path=graph)
    untrained.save_checkpoint(tmp_path / 'frozen')
    with pytest.raises(ValueError, match='trained'):
        distill_controller(graph, tmp_path / 'frozen', episodes, tmp_path / 'student')


def test_unaccepted_student_cannot_be_enabled(tmp_path):
    from flycode.training.distillation import distill_controller

    graph, episodes = teacher_and_episodes(tmp_path)
    output = tmp_path / 'student'
    # A zero-error tolerance must reject the nonzero ridge approximation.
    report = distill_controller(graph, tmp_path / 'teacher', episodes, output,
                               max_rmse=0., min_agreement=1.)
    assert not report['accepted']
    with pytest.raises(ValueError, match='validation'):
        make_brain('functional', checkpoint_path=output)


def test_student_learns_nonlinear_teacher_response_without_lowering_gate(tmp_path):
    from flycode.brain.functional_fly import ALL_MODES
    from flycode.domain import VALID_TOOLS
    from flycode.training.checkpoint import Dynamics
    from flycode.training.distillation import distill_controller

    graph = tmp_path / 'graph'
    graph_from_edges(np.arange(64), np.arange(64), np.roll(np.arange(64), 1), np.ones(64)).save(graph)
    teacher = make_brain('full', connectome_path=graph)
    teacher._input[:] = 0.
    teacher._input[0, [0, 12]] = [8., -1.6]
    teacher._input[1, [0, 12]] = [8., -6.4]
    teacher._input[2, 12] = 1.
    teacher._readout[:] = 0.
    teacher._readout[len(ALL_MODES) + VALID_TOOLS.index('read_file'), :3] = [2., -2., -2.]
    teacher.dynamics = Dynamics(recurrent_gain=0., leak=1.)
    teacher._learned_tools = True  # Explicit test boundary parameters, not a biological claim.
    teacher.save_checkpoint(tmp_path / 'teacher')
    episodes = [[TrainingStep(observation=Observation(task='curve', task_id=str(episode), step_number=step,
                    tests={'status': 'failed'}).model_dump(), chosen_action={'tool': 'read_file'},
                    outcome={'success': True}) for step in [0, 12, 24]] for episode in range(4)]
    report = distill_controller(graph, tmp_path / 'teacher', episodes, tmp_path / 'student')
    assert report['accepted'], report
    assert report['validation']['tool_agreement'] == 1.
    assert report['validation']['rmse'] < .01
    assert report['validation_method'] == 'functional controller replay'


def test_legacy_linear_student_checkpoint_remains_loadable(tmp_path):
    from flycode.domain import VALID_TOOLS
    from flycode.training.student import save_student

    weights = np.zeros((len(VALID_TOOLS), 25))
    weights[VALID_TOOLS.index('read_file'), -1] = 1.
    save_student(tmp_path, weights, {'accepted': True})
    student = make_brain('functional', checkpoint_path=tmp_path)
    state = student.step(make_stimulus(Observation(task='legacy')))
    assert max(state.action_bias, key=state.action_bias.get) == 'read_file'
