"""Legacy source ownership and owned execution of project-code tools."""

import hashlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..domain import Observation, ToolAction
from ..environment import SoftwareEnvironment
from ..orchestration.sources import Sources
from ..orchestration.store import Store, StoreError, default_state_path
from ..orchestration.transport import HostTransport
from ..tools import run_tool
from ..tools.filesystem import ToolResult
from ..verification.custody import locked, run


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    root: str
    action: ToolAction


class ToolReply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    success: bool
    stdout: str = ''
    stderr: str = ''
    payload: dict = Field(default_factory=dict)
    error_message: str = ''


def external_effect(action):
    args = action.arguments
    return (action.tool == 'postgres_query'
        or action.tool == 'run_tests' and bool(args.get('service'))
        or action.tool == 'docker_compose' and args.get('action', 'ps') != 'ps'
        or action.tool == 'alembic' and args.get('action', 'current') not in {'current', 'heads', 'history'}
        or action.tool == 'http_request' and str(args.get('method', 'GET')).upper() not in {'GET', 'HEAD', 'OPTIONS'})


class SourceExecution:
    def __init__(self, root: Path, directory: Path, owner_id: str, *, claim_root: Path | None = None):
        self.root, self.directory, self.owner_id = root.resolve(strict=True), directory, owner_id
        self.database = default_state_path()
        path = directory / 'coordinator.lock'
        self.coordinator = locked(path, create=True)
        descriptor = self.coordinator.__enter__()
        try:
            info = os.fstat(descriptor)
            with self.sources() as sources:
                self.claim = sources.acquire(sources.prepare(claim_root or self.root), owner_kind='legacy', owner_id=owner_id,
                    coordinator_path=path, coordinator_identity=(info.st_dev, info.st_ino))
        except BaseException:
            self.coordinator.__exit__(*sys.exc_info())
            raise

    @contextmanager
    def sources(self):
        store = Store(self.database)
        try:
            yield Sources(store)
        finally:
            store.close()

    def guard(self):
        with self.sources() as sources:
            claim = sources.guard(self.claim.id, self.owner_id)
            if (sources.prepare(Path(claim.root.path)) != claim.root
                    or not self.root.resolve(strict=True).is_relative_to(Path(claim.root.path))):
                raise StoreError('source_changed')

    def finish(self):
        self.coordinator.__exit__(None, None, None)
        with self.sources() as sources:
            return sources.settle(self.claim.id)

    def executor(self, base_url, model_id, role, stop, timeout, sink):
        transport = HostTransport(base_url, model_id, role, owner_id=self.owner_id, database=self.database)

        def execute(request):
            self.guard()
            return transport.execute(request, should_stop=stop, timeout=timeout,
                on_wait=lambda: sink('model_wait', {'role': role, 'model': model_id}))

        return execute

    def tool(self, action, should_stop):
        self.guard()
        if should_stop():
            return ToolResult(success=False, error_message='Cancelled before tool execution')
        if action.tool in {'read_file', 'list_files', 'search', 'edit_file', 'create_file'}:
            return run_tool(action.tool, self.root, **action.arguments)
        output = self.directory / 'tools' / uuid4().hex
        output.mkdir(parents=True, mode=0o700)
        request = ToolRequest(root=str(self.root), action=action).model_dump_json().encode()
        job = output / 'request.json'
        job.write_bytes(request)
        environment = {name: os.environ[name] for name in
            ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'VIRTUAL_ENV') if name in os.environ}
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])

        def prepare(ref):
            with self.sources() as sources:
                sources.guard(self.claim.id, self.owner_id)
                sources.bind_process(self.claim.id, ref)
                if external_effect(action):
                    sources.note_external(self.claim.id)

        with (output / 'stdout.txt').open('wb') as stdout, (output / 'stderr.txt').open('wb') as stderr:
            receipt = run([sys.executable, '-m', 'flycode.web.tool_worker', str(job),
                hashlib.sha256(request).hexdigest()], self.root, environment, stdout, stderr, output,
                300., should_stop, lambda state: None, lambda ref: None,
                on_prepare=prepare, version='tool-custody-v1')
        if receipt.reason not in {'completed', 'descendants_terminated'} or receipt.exit_code != 0:
            return ToolResult(success=False, error_message=f'Tool process stopped: {receipt.reason}',
                payload={'status': 'cancelled' if should_stop() else 'error', 'process': receipt.ref.id})
        result = output / 'result.json'
        if result.stat().st_size > 2 * 1024 ** 2:
            return ToolResult(success=False, error_message='Tool result exceeded size limit')
        reply = ToolReply.model_validate_json(result.read_text())
        return ToolResult(**reply.model_dump())


class LegacyEnvironment(SoftwareEnvironment):
    def __init__(self, execution: SourceExecution, *, seed, should_stop):
        super().__init__(execution.root, seed=seed)
        self.execution, self.should_stop = execution, should_stop

    async def reset(self, task):
        self.execution.guard()
        return await super().reset(task)

    def _run_tool(self, action):
        return self.execution.tool(action, self.should_stop)

    def _observe(self, **arguments):
        result = self.execution.tool(ToolAction(id=uuid4().hex, tool='__observe__', arguments=arguments),
                                     self.should_stop)
        if not result.success:
            raise InterruptedError(result.error_message)
        return Observation.model_validate(result.payload['observation'])
