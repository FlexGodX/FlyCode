"""Test-only gates around real browser reads and source installation."""

import json
import os
import time
from pathlib import Path


def wait_for(path, seconds):
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f'Gate deadline: {path.name}'
        time.sleep(.01)


def instrument_app(patch, gate):
    import test_application_coordinator

    prefix = ('import sys\n'
        f'sys.path.insert(0, {str(Path(__file__).parent)!r})\n'
        'from owner_race import observe_negative\n'
        f'observe_negative({str(gate)!r})\n')
    patch.setattr(test_application_coordinator, 'APP', prefix + test_application_coordinator.APP)


def observe_negative(directory):
    from flycode.orchestration.completions import Completions

    gate = Path(directory)
    original = Completions.current

    def observed(service, scope, identity):
        result = original(service, scope, identity)
        if result or not (gate / 'armed').exists() or (gate / 'released').exists():
            return result
        assert not service.store.db.in_transaction
        proof = service.get(scope, identity)
        task = service.store.task(scope, proof.task_id)
        workflow = service.store.workflow(scope, proof.workflow_id)
        try:
            descriptor = os.open(gate / 'blocked.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'w') as output:
                json.dump({'completion_id': identity, 'task_id': task.id,
                    'task_revision': task.revision, 'workflow_revision': workflow.revision,
                    'negative': result is False, 'in_transaction': False}, output)
            (gate / 'ready').touch()
        wait_for(gate / 'released', 120)
        return result

    Completions.current = observed


def instrument_application(patch, gate, source, store):
    from flycode.orchestration.application import Application

    original = Application._move

    def move(application, *args, **kwargs):
        result = original(application, *args, **kwargs)
        if application.source == source and not (gate / 'armed').exists():
            assert not store.db.in_transaction
            (gate / 'armed').touch()
            wait_for(gate / 'ready', 15)
        return result

    patch.setattr(Application, '_move', move)


def release(p, gate):
    wait_for(gate / 'ready', 1)
    observed = json.loads((gate / 'blocked.json').read_text())
    store, owner = p['store'], p['owner']
    task = store.task(owner, observed['task_id'])
    workflow = store.workflow(owner, p['workflow'].id)
    assert observed['negative'] and not observed['in_transaction']
    assert task.revision != observed['task_revision'] and workflow.revision != observed['workflow_revision']
    assert task.result_id is not None and workflow.state == 'succeeded'
    observed.update(published_task_revision=task.revision, published_workflow_revision=workflow.revision,
        integration_id=task.result_id)
    (gate / 'interleaving.json').write_text(json.dumps(observed, indent=2) + '\n')
    (gate / 'released').touch()
    return observed
