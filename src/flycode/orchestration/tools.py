"""Authorize local tool effects and record durable spending before execution."""

import hashlib
import json
import math
import stat
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from ..domain import ToolAction
from ..domain.orchestration import BudgetAmount, ExecutionPermission, ToolCall
from .store import Store, StoreError, changed, covers

SUPPORTED = {'read_file', 'list_files', 'search', 'edit_file', 'create_file', 'run_tests'}
SKIP = {'.git', '.venv', '__pycache__', 'node_modules'}
ARGUMENTS = {
    'read_file': {'path'}, 'list_files': {'path'}, 'search': {'path', 'pattern'},
    'create_file': {'path', 'content'}, 'edit_file': {'path', 'content', 'old_text', 'new_text'},
    'run_tests': {'target', 'timeout', 'service'},
}


def _path(root: Path, value: str) -> None:
    try:
        ExecutionPermission(read_scopes=(value,))
        # Protected names must remain protected on case-insensitive local filesystems.
        ExecutionPermission(read_scopes=(value.casefold(),))
    except (ValidationError, TypeError) as error:
        raise StoreError('permission', 'Expected an allowed project-relative path') from error
    for item in (root / value, *(root / value).parents):
        if item.is_symlink():
            raise StoreError('permission', 'Symbolic links are not supported')
        if item.exists():
            info = item.stat()
            if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise StoreError('permission', 'Special files and hard links are not supported')
        if item == root:
            break
    if not (root / value).resolve().is_relative_to(root):
        raise StoreError('permission', 'Path escapes the project')


def authorize(root: Path, action: ToolAction, permission: ExecutionPermission, remaining: float) -> ToolAction:
    if action.tool not in SUPPORTED:
        raise StoreError('unsupported_tool', 'This tool has no owned local execution profile')
    if not permission.approved or action.tool not in permission.tools:
        raise StoreError('permission', 'Tool is not approved')
    args = dict(action.arguments)
    if set(args) - ARGUMENTS[action.tool]:
        raise StoreError('permission', 'Unsupported tool arguments')
    if action.tool == 'run_tests':
        if args.get('service') or permission.read_scopes != ('.',):
            raise StoreError('permission', 'Local pytest requires full project read permission')
        target = args.get('target', '')
        if not isinstance(target, str) or target.startswith('-'):
            raise StoreError('permission', 'Pytest target must be a path, not an option')
        if target:
            _path(root, target.split('::', 1)[0])
        timeout = args.get('timeout', 60.)
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise StoreError('permission', 'Expected a positive bounded timeout')
        args['timeout'] = min(timeout, 60., remaining)
    else:
        path = args.get('path', '.' if action.tool in {'list_files', 'search'} else '')
        _path(root, path)
        if action.tool in {'read_file', 'list_files', 'search', 'edit_file'} and not covers(permission.read_scopes, (path,)):
            raise StoreError('permission', 'Path is outside read scopes')
        if action.tool in {'create_file', 'edit_file'} and not covers(permission.write_scopes, (path,)):
            raise StoreError('permission', 'Path is outside write scopes')
        if action.tool in {'list_files', 'search'}:
            # These tools recurse. Refuse unsafe descendants before they can read any bytes.
            for item in (root / path).rglob('*'):
                relative = item.relative_to(root)
                if not set(relative.parts) & SKIP:
                    _path(root, relative.as_posix())
    return action.model_copy(update={'arguments': args})


class Tools:
    def __init__(self, store: Store):
        self.store = store

    def get(self, scope, identity) -> ToolCall:
        call = self.store._load('tool_calls', identity, ToolCall)
        self.store.workflow(scope, call.workflow_id)
        return call

    def calls(self, scope, workflow_id) -> list[ToolCall]:
        self.store.workflow(scope, workflow_id)
        return [self.get(scope, row['id']) for row in self.store.db.execute('''SELECT tool_calls.id FROM tool_calls
            JOIN attempts ON attempts.id=attempt_id WHERE workflow_id=? ORDER BY tool_calls.rowid''', (workflow_id,))]

    def begin(self, scope, attempt_id, fence, *, root: Path, action: ToolAction) -> tuple[ToolCall, ToolAction]:
        store = self.store
        if store.db.in_transaction:
            raise RuntimeError('Tool authorization cannot run in a SQLite transaction')
        _, task, workflow = store._owned(scope, attempt_id, fence, running=True)
        action = authorize(root, action, task.permissions, workflow.deadline_at - store.clock())
        digest = hashlib.sha256(json.dumps(action.arguments, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with store.transaction():
            attempt, task, workflow = store._owned(scope, attempt_id, fence, running=True)
            found, _, request_hash = store._replay(scope, 'tool:' + attempt_id + ':' + action.id,
                {'tool': action.tool, 'arguments': digest, 'fence': fence})
            if found:
                raise StoreError('request_replay', 'A recorded tool must not be executed again')
            if task.paused or workflow.state != 'running' or store._counter('queue_paused'):
                raise StoreError('paused')
            if store.db.execute("SELECT 1 FROM tool_calls WHERE attempt_id=? AND state IN ('running','uncertain')",
                                (attempt_id,)).fetchone():
                raise StoreError('resource_busy', 'One tool may execute per attempt')
            call = ToolCall(id=uuid4().hex, workflow_id=workflow.id, task_id=task.id, attempt_id=attempt_id,
                instance_id=attempt.instance_id, fence=fence, tool=action.tool, argument_hash=digest, started_at=store.clock())
            store._debit(scope, attempt, task, workflow, BudgetAmount(tool_calls=1))
            store.db.execute('INSERT INTO tool_calls VALUES (?,?,?,?)',
                             (call.id, attempt_id, call.status, call.model_dump_json()))
            store._event(scope, workflow, 'tool_start', task=task, attempt=attempt, call=call.model_dump(mode='json'))
            store._remember(scope, 'tool:' + attempt_id + ':' + action.id, task.project_id, request_hash, call)
        return call, action

    def finish(self, scope, identity, *, status: str, error_class: str | None = None) -> ToolCall:
        """Trusted host acknowledgement; never accept worker text as proof of stop."""
        store = self.store
        store._role(scope, ('runtime',))
        if status not in {'completed', 'failed', 'uncertain'}:
            raise StoreError('tool_status')
        with store.transaction():
            call = self.get(scope, identity)
            if call.status != 'running':
                if (call.status, call.error_class) == (status, error_class):
                    return call
                if call.status != 'uncertain' or status == 'uncertain':
                    raise StoreError('reconcile_required', 'A terminal tool outcome cannot be overwritten')
            try:
                attempt, task, workflow = store._owned(scope, call.attempt_id, call.fence, running=True)
                late = False
            except StoreError as error:
                if error.code not in {'fence', 'wall_deadline'}:
                    raise
                attempt = store._attempt(call.attempt_id)
                task, workflow = store.task(scope, call.task_id), store.workflow(scope, call.workflow_id)
                late = True
            call = changed(call, status=status, error_class=error_class, late=late,
                           ended_at=store.clock() if status != 'uncertain' else None)
            store.db.execute('UPDATE tool_calls SET state=?,data=? WHERE id=?', (status, call.model_dump_json(), identity))
            if status == 'uncertain':
                store.db.execute('UPDATE reservations SET uncertain=1 WHERE attempt_id=?', (call.attempt_id,))
            store._event(scope, workflow, 'tool_uncertain' if status == 'uncertain' else 'tool_end',
                         task=task, attempt=attempt, call=call.model_dump(mode='json'))
            return call
