"""Permission errors never prove exit; cleanup requires confirmed group absence."""

import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from flycode.verification import runner


def test_naturally_exited_owned_leader_is_reaped_before_group_absence_check():
    process = subprocess.Popen([sys.executable, '-c', 'import os; os._exit(0)'], start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while True:
            state = subprocess.run(['ps', '-p', str(process.pid), '-o', 'stat='],
                                   capture_output=True, text=True, check=False).stdout
            if state.strip().startswith('Z'):
                break
            assert time.monotonic() < deadline
            time.sleep(.01)
        runner._terminate(process)
        assert process.returncode == 0
        assert runner._group_gone(process.pid)
    finally:
        process.wait(timeout=5)


def test_permission_race_requires_confirmed_absence_before_accepting_cleanup(monkeypatch):
    calls = []

    def killpg(pid, sig):
        calls.append(sig)
        if sig == signal.SIGTERM:
            raise PermissionError('group is departing')
        raise ProcessLookupError('group is gone')

    monkeypatch.setattr(runner.os, 'killpg', killpg)
    runner._terminate(SimpleNamespace(pid=123, wait=lambda timeout: 0))
    assert calls == [signal.SIGTERM, 0]


def test_persistent_permission_error_stays_unconfirmed_and_never_sends_kill(monkeypatch):
    calls = []
    clock = iter(range(100))
    monkeypatch.setattr(runner.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(runner.time, 'sleep', lambda duration: None)

    def killpg(pid, sig):
        calls.append(sig)
        raise PermissionError('no permission')

    monkeypatch.setattr(runner.os, 'killpg', killpg)
    with pytest.raises(PermissionError):
        runner._terminate(SimpleNamespace(pid=123, wait=lambda timeout: 0))
    assert calls.count(0) > 0
    assert signal.SIGKILL not in calls


def test_final_kill_must_be_followed_by_confirmed_absence(monkeypatch):
    calls, alive = [], [True]
    clock = iter(range(100))
    monkeypatch.setattr(runner.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(runner.time, 'sleep', lambda duration: None)

    def killpg(pid, sig):
        calls.append(sig)
        if sig == signal.SIGKILL:
            alive[0] = False
        elif not alive[0]:
            raise ProcessLookupError('gone')

    monkeypatch.setattr(runner.os, 'killpg', killpg)
    runner._terminate(SimpleNamespace(pid=123, wait=lambda timeout: 0))
    assert signal.SIGKILL in calls
    assert calls[-1] == 0
