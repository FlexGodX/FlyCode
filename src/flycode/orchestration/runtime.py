"""Supervise owned agent groups independently from blocking parent-side HTTP."""

import hashlib
import os
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from ..agent import RunResult
from ..brain import BRAIN_REGISTRY
from ..domain import VALID_TOOLS, TaskSpec, ToolAction
from ..domain.orchestration import (
    MessageDraft,
    MessageProgress,
    WorkerConfig,
    WorkerRecord,
    WorkerReply,
    WorkerRequest,
)
from ..llm.protocol import CortexContext
from ..verification.evidence import atomic_json
from ..verification.runner import _group_gone, _terminate
from . import wire
from .artifacts import Artifacts
from .cortex import AttemptCortex
from .integration import Integration
from .messages import Messages
from .store import Store, StoreError, changed
from .tools import SUPPORTED, Tools
from .workspaces import Workspaces


class Runtime:
    def __init__(self, store: Store, root: Path):
        self.store, self.root = store, Path(root).resolve()
        self.workspaces = Workspaces(store, self.root / 'workspaces')

    def worker(self, scope, attempt_id) -> WorkerRecord:
        self.store.task(scope, self.store._attempt(attempt_id).task_id)
        row = self.store.db.execute('SELECT data FROM workers WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row is None:
            raise StoreError('not_found', 'worker')
        return WorkerRecord.model_validate_json(row['data'])

    def launch(self, scope, attempt_id, fence, source: Path, *, brain='functional', seed=0, trust='trusted-local-process',
               message_recipients=()):
        if trust != 'trusted-local-process':
            raise StoreError('unsupported_sandbox', 'Hostile code requires a supported sandbox')
        if os.name != 'posix':
            raise StoreError('unsupported_platform', 'Owned process groups require POSIX')
        attempt, task, _ = self.store._owned(scope, attempt_id, fence)
        self.store._execution_available(scope, attempt_id)
        if (task.permissions is None or task.permissions.read_scopes != ('.',)
                or set(task.permissions.tools) - (SUPPORTED | {'stop'}) or attempt.profile_snapshot.role == 'decider'):
            raise StoreError('unsupported_profile')
        if brain not in BRAIN_REGISTRY:
            raise StoreError('brain')
        if message_recipients:
            route = MessageDraft(recipients=message_recipients, idempotency_key='route-validation',
                content=MessageProgress(kind='progress', summary='Route admission.'))
            message_recipients = route.recipients
            Messages(self.store).recipients(scope, attempt, message_recipients)
        workspace = self.workspaces.prepare(scope, attempt_id, fence, source)
        Integration(self.store, self.root).begin_resolution(scope, attempt_id, fence, workspace)
        directory = self.workspaces.root / workspace.id
        record = WorkerRecord(id=uuid4().hex, attempt_id=attempt_id, fence=fence, created_at=self.store.clock())
        with self.store.transaction():
            self.store._claim_execution(scope, attempt_id, fence, 'worker')
            self.store.db.execute('INSERT INTO workers VALUES (?,?,?)', (record.id, attempt_id, record.model_dump_json()))
        task_spec = TaskSpec(id=task.id, task=task.objective, seed=seed, max_steps=attempt.reserved.tool_calls + 1,
            max_tool_calls=attempt.reserved.tool_calls + 1)
        config = WorkerConfig(database=str(self.store.path.resolve()), repository=str(directory / 'repository'),
            scope=scope, attempt_id=attempt_id, fence=fence, task=task_spec, brain=brain, seed=seed, message_recipients=message_recipients)
        return WorkerHandle(self, scope, record, directory, config)


class WorkerHandle:
    def __init__(self, runtime: Runtime, scope, record, directory, config):
        self.runtime, self.scope, self.record, self.directory = runtime, scope, record, directory
        self.finished, self.process_stopped, self.stopping, self.rpc_done = Event(), Event(), Event(), Event()
        self.stderr_done, self.stderr_metadata = Event(), None
        self.result, self.error = None, None
        self.message_recipients = frozenset(config.message_recipients)
        self.launch_ready, self.launch_failed = Event(), False
        channels: list[socket.socket] = []
        child_rpc = child_control = None
        try:
            self.rpc, child_rpc = socket.socketpair()
            channels.extend((self.rpc, child_rpc))
            self.control, child_control = socket.socketpair()
            channels.extend((self.control, child_control))
            home = directory / 'home'
            home.mkdir()
            environment = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(home), 'LANG': 'en_US.UTF-8',
                'PYTHONPATH': str(Path(__file__).resolve().parents[2]), 'PYTHONNOUSERSITE': '1',
                'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
            self.process = subprocess.Popen([sys.executable, '-m', 'flycode.orchestration.worker',
                str(child_rpc.fileno()), str(child_control.fileno())], cwd=directory, env=environment,
                pass_fds=(child_rpc.fileno(), child_control.fileno()), start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.record = changed(record, pid=self.process.pid)
            runtime.store.db.execute('UPDATE workers SET data=? WHERE id=?', (self.record.model_dump_json(), record.id))
            wire.send(self.rpc, config.model_dump(mode='json'))
            Thread(target=self._await_launch, args=(self._capture_stderr,), name='flycode-worker-stderr', daemon=True).start()
            Thread(target=self._await_launch, args=(self._serve,), name='flycode-worker-rpc', daemon=True).start()
            Thread(target=self._await_launch, args=(self._supervise,), name='flycode-worker-supervisor', daemon=True).start()
        except BaseException as error:
            self.launch_failed = True
            for channel in channels:
                channel.close()
            stopped = not hasattr(self, 'process')
            if hasattr(self, 'process'):
                try:
                    _terminate(self.process)
                    stopped = _group_gone(self.process.pid)
                except (OSError, subprocess.SubprocessError):
                    stopped = False
                if self.process.stderr is not None:
                    self.process.stderr.close()
            self.record = changed(self.record, state='exited' if stopped else 'uncertain',
                stop_confirmed=stopped, stopped_at=runtime.store.clock() if stopped else None,
                exit_code=self.process.returncode if hasattr(self, 'process') else None, error=type(error).__name__)
            with runtime.store.transaction():
                runtime.store.db.execute('UPDATE workers SET data=? WHERE id=?', (self.record.model_dump_json(), record.id))
                if not stopped:
                    attempt = runtime.store._attempt(record.attempt_id)
                    runtime.store._reconcile(scope, runtime.store._workflow(attempt.workflow_id), attempt, 'launch_uncertain')
            if stopped:
                runtime.store.confirm_stopped(scope, record.attempt_id, record.fence, outcome='failed', expenditure_confirmed=True)
            raise
        finally:
            for child_channel in (child_rpc, child_control):
                if child_channel is not None:
                    child_channel.close()
            self.launch_ready.set()

    def _await_launch(self, target):
        # Neither thread admits work until both supervision and RPC are available.
        self.launch_ready.wait()
        if not self.launch_failed:
            target()

    def _connection(self):
        original = self.runtime.store
        return Store(original.path, catalogue=original.catalogue, clock=original.clock)

    def _capture_stderr(self):
        observed, retained, digest = 0, 0, hashlib.sha256()
        try:
            with (self.directory / 'worker-stderr.log').open('xb') as output:
                while chunk := os.read(self.process.stderr.fileno(), 4096):
                    observed += len(chunk)
                    bounded = chunk[:max(0, 65536 - retained)]
                    output.write(bounded)
                    output.flush()
                    digest.update(bounded)
                    retained += len(bounded)
                os.fsync(output.fileno())
            self.stderr_metadata = {'path': 'worker-stderr.log', 'sha256': digest.hexdigest(),
                'observed_bytes': observed, 'retained_bytes': retained, 'truncated': observed > retained}
        except OSError as error:
            self.stderr_metadata = {'error_class': type(error).__name__}
            self.error = type(error).__name__ + ':stderr_capture'
        finally:
            self.process.stderr.close()
            self.stderr_done.set()

    def _serve(self):
        connection = None
        cortex = None
        sequence = 0
        try:
            connection = self._connection()
            while True:
                request = WorkerRequest.model_validate(wire.receive(self.rpc))
                if request.sequence != sequence + 1:
                    raise StoreError('worker_sequence')
                sequence = request.sequence
                payload = {}
                if request.operation == 'ready':
                    if cortex is not None:
                        raise StoreError('worker_ready')
                    connection.confirm_started(self.scope, self.record.attempt_id, self.record.fence)
                    cortex = AttemptCortex(connection, self.scope, self.record.attempt_id, self.record.fence, self.stopping.is_set,
                        message_recipients=tuple(sorted(self.message_recipients)), artifact_root=self.runtime.root)
                    self.record = changed(self.record, state='running')
                    with connection.transaction():
                        connection._owned(self.scope, self.record.attempt_id, self.record.fence, running=True)
                        connection.db.execute('UPDATE workers SET data=? WHERE id=?', (self.record.model_dump_json(), self.record.id))
                elif request.operation == 'error':
                    connection._owned(self.scope, self.record.attempt_id, self.record.fence)
                    self.error = 'worker:' + str(request.payload.get('error_class', 'Unknown'))[:100]
                else:
                    attempt, task, workflow = connection._owned(self.scope, self.record.attempt_id, self.record.fence, running=True)
                    if request.operation == 'candidates':
                        candidates = cortex.generate_candidates(CortexContext.model_validate(request.payload))
                        connection._owned(self.scope, attempt.id, attempt.fence, running=True)
                        payload = {'candidates': [item.model_dump(mode='json') for item in candidates],
                            'model_calls': cortex.model_calls, 'routing': cortex.last_routing.model_dump(mode='json')}
                    elif request.operation == 'artifact_tool':
                        result = Artifacts(connection, self.runtime.root).read_input(self.scope, attempt.id, attempt.fence,
                            ToolAction.model_validate(request.payload))
                        payload = asdict(result)
                    elif request.operation == 'message_tool':
                        result = Messages(connection).execute(self.scope, attempt.id, attempt.fence,
                            root=self.directory / 'repository', action=ToolAction.model_validate(request.payload),
                            routes=self.message_recipients)
                        payload = asdict(result)
                    elif request.operation == 'message':
                        draft = MessageDraft.model_validate(request.payload)
                        if not set(draft.recipients) <= self.message_recipients:
                            raise StoreError('message_route')
                        try:
                            messages = Messages(connection).send(self.scope, attempt.id, attempt.fence, draft)
                            payload = {'message_ids': [item.id for item in messages]}
                        except StoreError as error:
                            if error.code not in {'recipient', 'recipient_workflow', 'message_scope', 'message_budget'}:
                                raise
                            with connection.transaction():
                                connection._owned(self.scope, attempt.id, attempt.fence, running=True)
                                connection._event(self.scope, workflow, 'message_blocked', task=task, attempt=attempt,
                                    reason=error.code, message_kind=draft.content.kind)
                            payload = {'blocked': error.code}
                    elif request.operation == 'activity':
                        data = request.payload
                        kind = data.get('kind')
                        if not isinstance(kind, str) or len(kind) > 64:
                            raise StoreError('worker_activity')
                        step, mode, tool = data.get('step'), data.get('mode'), data.get('tool')
                        with connection.transaction():
                            connection._owned(self.scope, attempt.id, attempt.fence, running=True)
                            connection._event(self.scope, workflow, 'agent_activity', task=task, attempt=attempt,
                                event_kind=kind, step=step if isinstance(step, int) and 0 <= step <= 121 else None,
                                mode=mode if mode in {'explore','diagnose','plan','execute','verify','recover','consolidate','stop'} else None,
                                tool=tool if tool in VALID_TOOLS else None)
                    elif request.operation == 'result':
                        result = RunResult.model_validate(request.payload)
                        content = result.model_dump_json()
                        atomic_json(self.directory / 'run-result.json', content)
                        self.result = hashlib.sha256(content.encode()).hexdigest()
                wire.send(self.rpc, WorkerReply(sequence=sequence, payload=payload).model_dump(mode='json'))
                if request.operation in {'result', 'error'}:
                    break
        except (EOFError, OSError, ValueError, TypeError, AttributeError, sqlite3.Error) as error:
            if not self.stopping.is_set():
                self.error = type(error).__name__ + ':' + str(getattr(error, 'code', 'worker_channel'))
        finally:
            try:
                if cortex is not None:
                    cortex.core.reasoner._client.close()
            except (OSError, ValueError, RuntimeError) as error:
                self.error = type(error).__name__ + ':client_cleanup'
            finally:
                try:
                    if connection is not None:
                        connection.close()
                finally:
                    self.rpc_done.set()

    def _supervise(self):
        connection = None
        heartbeat, pulse = time.monotonic(), 0.
        try:
            connection = self._connection()
            while True:
                now = time.monotonic()
                connection.expire(self.scope)
                try:
                    attempt, task, workflow = connection._owned(self.scope, self.record.attempt_id, self.record.fence)
                    if task.paused or workflow.state != 'running' or connection._counter('queue_paused'):
                        raise StoreError('paused')
                    if now - heartbeat >= 10:
                        connection.heartbeat(self.scope, attempt.id, attempt.fence)
                        heartbeat = now
                except StoreError:
                    self.stopping.set()
                if now - pulse >= .5 and not self.process_stopped.is_set():
                    try:
                        self.control.sendall(b'.')
                    except OSError:
                        # A terminal worker closes control after its accepted
                        # result. That close is not an owner cancellation.
                        if self.result is None and not self.rpc_done.is_set():
                            self.stopping.set()
                    pulse = now
                if self.stopping.is_set() or self.error:
                    with connection.transaction():
                        attempt = connection._attempt(self.record.attempt_id)
                        if attempt.state in {'assigned', 'running'}:
                            connection._reconcile(self.scope, connection._workflow(attempt.workflow_id), attempt, 'runtime_stop')
                if (self.stopping.is_set() or self.rpc_done.is_set() or self.process.poll() is not None) and not self.process_stopped.is_set():
                    _terminate(self.process)
                    if not _group_gone(self.process.pid):
                        raise OSError('Owned worker group is not confirmed stopped')
                    self.process_stopped.set()
                    self.control.close()
                if self.process_stopped.is_set() and self.rpc_done.is_set():
                    break
                time.sleep(.05)
            if not self.stderr_done.wait(2):
                self.stderr_metadata = {'error_class': 'StderrCaptureIncomplete'}
            attempt = connection._attempt(self.record.attempt_id)
            task, workflow = connection.task(self.scope, attempt.task_id), connection.workflow(self.scope, attempt.workflow_id)
            if task.fence != attempt.fence or attempt.state != 'running' or workflow.state != 'running':
                self.stopping.set()
            publish = self.result is not None and not self.stopping.is_set()
            if not publish:
                # Group absence settles only local file tools. Unknown pytest effects need host reconciliation.
                for call in Tools(connection).calls(self.scope, workflow.id):
                    if call.attempt_id == attempt.id and call.status in {'running', 'uncertain'}:
                        if call.tool != 'run_tests':
                            Tools(connection).finish(self.scope, call.id, status='failed', error_class='WorkerStopped')
                        elif call.status == 'running':
                            Tools(connection).finish(self.scope, call.id, status='uncertain', error_class='WorkerStopped')
                try:
                    connection.confirm_stopped(self.scope, attempt.id, attempt.fence,
                        outcome='retry_wait' if task.paused or workflow.state == 'pausing' else 'failed', expenditure_confirmed=True)
                except StoreError as error:
                    if error.code not in {'resource_busy', 'uncertain'}:
                        raise
                    self.error = error.code
            self.record = changed(self.record, state='exited', stopped_at=connection.clock(), stop_confirmed=True,
                exit_code=self.process.returncode, result_hash=self.result, error=self.error)
            with connection.transaction():
                connection.db.execute('UPDATE workers SET data=? WHERE id=?', (self.record.model_dump_json(), self.record.id))
                connection._event(self.scope, workflow, 'worker_stopped', task=task, attempt=attempt,
                    worker_id=self.record.id, exit_code=self.process.returncode, result_hash=self.result,
                    stderr=self.stderr_metadata)
            if publish:
                try:
                    Artifacts(connection, self.runtime.root).publish(self.scope, attempt.id, attempt.fence)
                except StoreError as error:
                    with connection.transaction():
                        attempt = connection._attempt(attempt.id)
                        if attempt.state == 'running':
                            connection._reconcile(self.scope, workflow, attempt, 'artifact_' + error.code)
                        connection._event(self.scope, workflow, 'artifact_blocked', task=task, attempt=attempt, reason=error.code)
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
            self.error = type(error).__name__ + ':' + str(getattr(error, 'code', 'supervision'))
            self.stopping.set()
            self.record = changed(self.record, state='uncertain', error=self.error)
            try:
                if connection is not None:
                    with connection.transaction():
                        attempt = connection._attempt(self.record.attempt_id)
                        if attempt.state in {'assigned', 'running', 'stopping'}:
                            connection._reconcile(self.scope, connection._workflow(attempt.workflow_id), attempt, 'supervisor_error')
                        connection.db.execute('UPDATE workers SET data=? WHERE id=?', (self.record.model_dump_json(), self.record.id))
            except sqlite3.Error:
                pass  # Keep the last durable ownership; a failed write cannot authorize release.
        finally:
            self.control.close()
            if not self.process_stopped.is_set():
                try:
                    _terminate(self.process)
                    if _group_gone(self.process.pid):
                        self.process_stopped.set()
                except (OSError, subprocess.SubprocessError):
                    pass  # No ownership release occurs without confirmed group absence.
            try:
                self.rpc.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.rpc.close()
            try:
                if connection is not None:
                    connection.close()
            finally:
                self.finished.set()

    def close(self):
        self.stopping.set()
        self.control.close()
        if not self.process_stopped.wait(6):
            _terminate(self.process)
