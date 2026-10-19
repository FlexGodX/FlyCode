"""Hold a real terminal receipt lock inside the owned browser-test daemon."""

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from flycode.verification import custody


def instrument(path, mode):
    path = Path(path)
    original, finish = custody.stopped, custody.Launch.finish
    local, selection = threading.local(), threading.Lock()
    selected = False
    writing = threading.Lock()
    record = {'mode': mode, 'blocked_reads': 0}

    def save():
        with writing:
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(record, indent=2) + '\n')
            temporary.replace(path)

    def owned_finish(self):
        local.launch = self
        try:
            return finish(self)
        finally:
            local.launch = None

    @contextmanager
    def stopped(ref):
        nonlocal selected
        launch = getattr(local, 'launch', None)
        holder = None
        if launch is not None and ref.version == 'pytest-custody-v1':
            with selection:
                if not selected:
                    # The owned guardian has exited before finish reads custody.
                    assert launch.supervisor.poll() is not None
                    with original(ref) as receipt:
                        positive = receipt.started and receipt.reason == 'completed' and receipt.exit_code == 0
                    if positive:
                        selected = True
                        ready = threading.Event()
                        errors = []
                        record.update(ref=ref.model_dump(mode='json'), guardian_exit=launch.supervisor.returncode,
                            locked_at=time.monotonic())

                        def hold():
                            try:
                                with original(ref):
                                    record['locked'] = True
                                    save()
                                    ready.set()
                                    time.sleep(7 if mode == 'held' else .4)
                                record['released'] = True
                                record['released_at'] = time.monotonic()
                                save()
                            except (OSError, ValueError) as error:
                                errors.append(repr(error))
                                ready.set()

                        holder = threading.Thread(target=hold)
                        holder.start()
                        assert ready.wait(3) and not errors, errors
        try:
            try:
                with original(ref) as receipt:
                    if selected and ref.id == record['ref']['id']:
                        record['validated'] = receipt.model_dump(mode='json')
                        record['validated_at'] = time.monotonic()
                        save()
                    yield receipt
            except BlockingIOError:
                if selected and ref.id == record['ref']['id']:
                    record['blocked_reads'] += 1
                    record.setdefault('first_blocked_at', time.monotonic())
                    record['last_blocked_at'] = time.monotonic()
                    save()
                raise
        finally:
            # A held-lock failure must outlive the retry deadline, while the
            # test-owned reader still has a bounded cleanup owner.
            if holder is not None and mode != 'held':
                holder.join(timeout=3)
                assert not holder.is_alive()

    custody.Launch.finish = owned_finish
    custody.stopped = stopped
