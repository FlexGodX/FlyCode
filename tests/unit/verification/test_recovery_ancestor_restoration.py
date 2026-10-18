"""Recovery notices restoration of a pinned execution ancestor."""
import time
from pathlib import Path

from test_registered_coordinator import coordinator, until
from test_verification_execution import queued
from test_verification_recovery import ledger

from flycode.orchestration.store import Store
from flycode.verification.control import VerificationControl
from flycode.verification.custody import stopped


def test_restored_execution_ancestor_retries_without_host_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_HOST_AUTHORITY_ROOT', str(tmp_path / 'authority'))
    marker = tmp_path / 'started'
    database, _, _, _, execution = queued(tmp_path,
        'from pathlib import Path\nimport time\ndef test_sleep():\n'
        f'    Path({str(marker)!r}).touch()\n    time.sleep(60)\n')
    store = Store(database)
    try:
        control = VerificationControl(store)
        with coordinator(database, tmp_path / 'host-state') as (old, _):
            until(marker.exists)
            old.kill()
            old.wait(timeout=5)
        original = control._execution(execution.id)
        def group_stopped():
            try:
                with stopped(original.invocations[0].process):
                    return True
            except (OSError, ValueError):
                return False
        until(group_stopped)
        root = Path(original.coordinator_root.path)
        parent, saved = root.parent, root.parent.with_name('saved-executions')
        parent.rename(saved)
        parent.mkdir()
        (saved / root.name).rename(root)
        with coordinator(database, tmp_path / 'host-state'):
            until(lambda: control._execution(execution.id).error == 'ownership_unknown')
            assert ledger(store, execution.id) == (1, 0, 1)
            time.sleep(1.2)
            root.rename(saved / root.name)
            parent.rmdir()
            saved.rename(parent)
            until(lambda: control._execution(execution.id).state == 'interrupted', timeout=5)
            assert ledger(store, execution.id) == (1, 0, 0)
    finally:
        store.close()
