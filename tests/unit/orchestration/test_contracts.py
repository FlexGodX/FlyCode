"""Permission/model roles and normalized scheduling features are explicit."""

import pytest


def test_reviewer_cannot_register_mutating_tools_without_a_write_scope():
    from flycode.domain.orchestration import AgentProfileDraft, ExecutionPermission

    with pytest.raises(ValueError, match='write|mutat'):
        AgentProfileDraft(role='reviewer', model_ref='ref', instructions='Review', project_ids=('p',),
            permissions=ExecutionPermission(approved=True, tools=('edit_file',)), context_limit=4096)


@pytest.mark.parametrize('path', ['/tmp/project', '../outside', 'src/../other', '.env', 'src/.git', 'src\\other'])
def test_file_permissions_reject_escaping_and_sensitive_scopes(path):
    from flycode.domain.orchestration import ExecutionPermission

    with pytest.raises(ValueError):
        ExecutionPermission(read_scopes=(path,))


def test_baseline_formula_and_eighth_dispatch_oldest_rule():
    from flycode.domain.orchestration import WorkItem
    from flycode.orchestration.scheduler import baseline, ordered

    old = WorkItem(id='old', short_id='FC-1', title='Old', project_id='p', workflow_id='w',
                   created_at=0, updated_at=0, queued_at=0, priority=0, expected_progress=0)
    urgent = WorkItem(id='urgent', short_id='FC-2', title='Urgent', project_id='p', workflow_id='w',
        created_at=100, updated_at=100, queued_at=100, priority=3, critical_path_fraction=1,
        expected_progress=1, risk=1, due_at=600)
    assert baseline(urgent, 600, 1) == pytest.approx(.30 + .20 + .15 + .15 * 500 / 600 + .10 - .05 - .05)
    assert ordered([old, urgent], 600, 6)[0].id == 'urgent'
    assert ordered([old, urgent], 600, 7)[0].id == 'old'
    assert ordered([old, urgent], 600, 7, root=True)[0].id == 'old'
