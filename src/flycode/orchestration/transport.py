"""Trusted host requests share the canonical SQLite endpoint ownership index."""

import hashlib
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar
from uuid import uuid4

from openai import APIStatusError
from pydantic import ValidationError

from ..domain.orchestration import HostModelCall, ModelUsage
from .store import Store, StoreError, changed, default_state_path

T = TypeVar('T')


class EndpointBusy(ValueError):
    pass


class HostTransport:
    def __init__(self, base_url: str, model_id: str, role: Literal['primary', 'scorer'], *,
                 kind: Literal['legacy', 'probe'] = 'legacy', owner_id: str | None = None, database: Path | None = None):
        self.database = Path(database) if database is not None else default_state_path()
        self.endpoint_ref = hashlib.sha256(base_url.rstrip('/').encode()).hexdigest()[:20]
        self.model_id, self.role, self.kind = model_id, role, kind
        self.owner_id = owner_id or uuid4().hex

    def _get(self, store: Store, identity: str) -> HostModelCall:
        row = store.db.execute('SELECT attempt_id,data FROM model_calls WHERE id=?', (identity,)).fetchone()
        if row is None or row['attempt_id'] is not None:
            raise StoreError('scope')
        call = HostModelCall.model_validate_json(row['data'])
        if (call.owner_id, call.owner_kind, call.endpoint_ref, call.model_id, call.role) != (
                self.owner_id, self.kind, self.endpoint_ref, self.model_id, self.role):
            raise StoreError('scope')
        return call

    def _finish(self, store, identity, *, status, usage=None, error_class=None, late=False):
        if status not in {'completed', 'failed', 'uncertain'}:
            raise StoreError('model_status')
        with store.transaction():
            call = self._get(store, identity)
            if call.status != 'running':
                if (call.status, call.usage, call.error_class, call.late) == (status, usage, error_class, late):
                    return call
                if call.status != 'uncertain' or status == 'uncertain':
                    raise StoreError('reconcile_required')
            call = changed(call, status=status, usage=usage, error_class=error_class, late=late,
                ended_at=store.clock() if status != 'uncertain' else None)
            store.db.execute('UPDATE model_calls SET state=?,data=? WHERE id=?', (status, call.model_dump_json(), call.id))
            return call

    def finish(self, identity: str, *, status: str, usage: ModelUsage | None = None,
               error_class: str | None = None) -> HostModelCall:
        """Exact trusted transport acknowledgment; never exposed as a browser command."""
        store = Store(self.database)
        try:
            return self._finish(store, identity, status=status, usage=usage, error_class=error_class)
        finally:
            store.close()

    def execute(self, request: Callable[[], T], *, should_stop: Callable[[], bool] = lambda: False,
                timeout: float = 120., on_wait: Callable[[], None] = lambda: None) -> T:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError('Endpoint wait limit must be finite and nonnegative')
        store = Store(self.database)
        deadline, waited = time.monotonic() + timeout, False
        try:
            while True:
                with store.transaction():
                    if should_stop():
                        raise InterruptedError('Model request cancelled before admission')
                    busy = store.db.execute("SELECT 1 FROM model_calls WHERE endpoint_ref=? AND state IN ('running','uncertain')",
                        (self.endpoint_ref,)).fetchone()
                    if not busy:
                        call = HostModelCall(id=uuid4().hex, owner_id=self.owner_id, owner_kind=self.kind,
                            endpoint_ref=self.endpoint_ref, model_id=self.model_id, role=self.role, started_at=store.clock())
                        store.db.execute('INSERT INTO model_calls VALUES (?,?,?,?,?)',
                            (call.id, None, call.endpoint_ref, call.status, call.model_dump_json()))
                        break
                if not waited:
                    on_wait()
                    waited = True
                if time.monotonic() >= deadline:
                    raise EndpointBusy('Model endpoint is occupied; no request sent')
                time.sleep(.05)
            try:
                result = request()
            except APIStatusError as error:
                self._finish(store, call.id, status='failed', error_class=type(error).__name__)
                raise
            except BaseException as error:
                self._finish(store, call.id, status='uncertain', error_class=type(error).__name__)
                raise
            raw_usage = getattr(result, 'usage', None)
            try:
                usage = ModelUsage(**{name: getattr(raw_usage, name, None) for name in ModelUsage.model_fields}) if raw_usage else None
            except ValidationError:
                self._finish(store, call.id, status='failed', error_class='UsageValidationError')
                raise
            late = should_stop()
            self._finish(store, call.id, status='completed', usage=usage, late=late)
            if late:
                raise InterruptedError('Cancelled provider response retained for audit only')
            return result
        finally:
            store.close()
