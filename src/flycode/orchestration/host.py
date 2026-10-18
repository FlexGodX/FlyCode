"""Physical, kernel-owned leadership for a managed canonical database."""

import hashlib
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.orchestration import AccessScope
from ..domain.source import SourceRoot
from ..verification.custody import locked
from ..verification.evidence import atomic_json
from .store import Store, StoreError, changed, host_pin_path


class HostRegistration(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    version: Literal['canonical-host-v1'] = 'canonical-host-v1'
    id: str
    database_path: str
    database_identity: tuple[int, int]
    database_parent: SourceRoot
    state_root: SourceRoot
    authority_root: SourceRoot
    runtime_root: SourceRoot
    lock_path: str
    lock_identity: tuple[int, int]


class HostSession(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    id: str
    registration_id: str
    generation: int = Field(ge=1)
    phase: Literal['recovering', 'ready', 'draining', 'stopped']
    heartbeat_at: float


def _root(path):
    path = Path(path).resolve(strict=True)
    if not path.is_dir():
        raise StoreError('host_identity')
    info = path.stat()
    return SourceRoot(path=str(path), identity=(info.st_dev, info.st_ino),
        ancestors=tuple((info.st_dev, info.st_ino) for info in (parent.stat() for parent in path.parents)))


def _file(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StoreError('host_identity')
    return info.st_dev, info.st_ino


def _read_registration(database):
    if not database.exists():
        return None
    db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, isolation_level=None)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='host_registry'").fetchone():
            return None
        row = db.execute('SELECT data FROM host_registry WHERE id=1').fetchone()
        return HostRegistration.model_validate_json(row[0]) if row else None
    finally:
        db.close()


class Host:
    def __init__(self, database: Path, state_root: Path | None = None):
        self.database = Path(database).expanduser().resolve()
        self.state_root = Path(state_root).expanduser().resolve() if state_root is not None else None
        self.closed = True
        self._used = False
        self._lock = None
        self.descriptor = None

    def __enter__(self):
        if self._used:
            raise StoreError('host_reused', 'A host context represents one immutable session')
        self._used = True
        try:
            self._start()
            self.recover()
            return self
        except BaseException:
            self._release()
            raise

    def __exit__(self, *exc):
        self.close()

    def _start(self):
        if not self.closed:
            raise StoreError('host_busy')
        if self.database.exists() and self.database.stat().st_nlink != 1:
            raise StoreError('host_database_link', 'Hard-linked SQLite paths are unsupported')
        home = self.state_root or Path.home() / '.local/share/flycode/hosts'
        key = hashlib.sha256(str(self.database).encode()).hexdigest()
        pin = host_pin_path(self.database)
        # Check the external path pin before opening a potentially replaced DB.
        prepared = self._pin(pin) if pin.exists() else None
        if prepared is not None:
            self._physical(prepared, pin)
        previous = _read_registration(self.database)
        if previous is not None:
            if self.state_root is not None and str(home) != previous.state_root.path:
                raise StoreError('host_identity')
            home = Path(previous.state_root.path)
            pin = host_pin_path(self.database)
            if not pin.exists() or self._pin(pin) != previous:
                raise StoreError('host_ownership_unknown', 'Initialized host pin is missing or changed')
            self._physical(previous, pin)
            prepared = previous
        home.mkdir(parents=True, exist_ok=True)
        pin.parent.mkdir(parents=True, exist_ok=True)
        runtime = home / key
        if prepared is None:
            runtime.mkdir(exist_ok=True)
        self.pin_path = pin
        # Initial schema creation is not queue recovery or execution authority.
        store = Store(self.database)
        try:
            if prepared is None and previous is None and self._manual_busy(store):
                raise StoreError('host_adoption_busy', 'Existing manual execution requires trusted quiescence')
            lock_path = Path(prepared.lock_path) if prepared else runtime / 'coordinator.lock'
            create = prepared is None and not lock_path.exists()
            self._lock = locked(lock_path, identity=prepared.lock_identity if prepared else None, create=create)
            try:
                self.descriptor = self._lock.__enter__()
            except (BlockingIOError, FileExistsError) as error:
                self._lock = None
                raise StoreError('host_busy') from error
            os.set_inheritable(self.descriptor, False)
            if prepared is None and not create:
                if not pin.exists():
                    raise StoreError('host_ownership_unknown', 'Incomplete initialization has no immutable reference')
                prepared = self._pin(pin)
                self._physical(prepared, pin)
            registration = prepared or HostRegistration(id=uuid4().hex, database_path=str(self.database),
                database_identity=_file(self.database), database_parent=_root(self.database.parent),
                state_root=_root(home), authority_root=_root(pin.parent.parent), runtime_root=_root(runtime), lock_path=str(lock_path), lock_identity=_file(lock_path))
            if prepared is None:
                atomic_json(pin, registration.model_dump_json())
                for path in (pin, pin.parent):
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            self._physical(registration, pin)
            with store.transaction():
                row = store.db.execute('SELECT data FROM host_registry WHERE id=1').fetchone()
                if row is None:
                    if prepared is not None:
                        raise StoreError('host_ownership_unknown', 'Initialized path pin has no database registration')
                    if self._manual_busy(store):
                        raise StoreError('host_adoption_busy', 'Existing manual execution requires trusted quiescence')
                    store.db.execute('INSERT INTO host_registry VALUES (1,?)', (registration.model_dump_json(),))
                elif HostRegistration.model_validate_json(row[0]) != registration:
                    raise StoreError('host_identity')
                row = store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()
                old = HostSession.model_validate_json(row[0]) if row else None
                if ((previous is not None and old is None)
                        or (old is not None and old.registration_id != registration.id)):
                    raise StoreError('host_ownership_unknown', 'Initialized host session is missing or changed')
                session = HostSession(id=uuid4().hex, registration_id=registration.id,
                    generation=old.generation + 1 if old else 1, phase='recovering', heartbeat_at=time.time())
                store.db.execute('INSERT INTO host_sessions VALUES (1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                    (session.model_dump_json(),))
            self.registration, self.session, self.closed = registration, session, False
        finally:
            store.close()

    @staticmethod
    def _pin(path):
        try:
            _file(path)
            return HostRegistration.model_validate_json(path.read_text())
        except (OSError, ValueError) as error:
            raise StoreError('host_ownership_unknown', 'Invalid host path pin') from error

    def _physical(self, registration, pin):
        try:
            if (registration.database_path != str(self.database)
                    or _file(self.database) != registration.database_identity
                    or _root(self.database.parent) != registration.database_parent
                    or _root(pin.parent.parent) != registration.authority_root
                    or _root(registration.state_root.path) != registration.state_root
                    or _root(registration.runtime_root.path) != registration.runtime_root
                    or _file(registration.lock_path) != registration.lock_identity
                    or self._pin(pin) != registration):
                raise StoreError('host_identity')
        except OSError as error:
            raise StoreError('host_identity') from error

    @staticmethod
    def _manual_busy(store):
        return (store.db.execute("SELECT 1 FROM attempts WHERE state IN ('assigned','running','stopping','reconciling')").fetchone()
            or store.db.execute("SELECT 1 FROM verification_executions WHERE state IN "
                "('preparing','running','evaluating','cancelling','reconciling')").fetchone()
            or store.db.execute('SELECT 1 FROM verification_reservations WHERE uncertain=1').fetchone()
            or store.db.execute("SELECT 1 FROM model_calls WHERE attempt_id IS NOT NULL AND state IN ('running','uncertain')").fetchone()
            or store.db.execute("SELECT 1 FROM tool_calls WHERE state IN ('running','uncertain')").fetchone()
            or store.db.execute("SELECT 1 FROM integrations WHERE state IN ('preparing','verifying','applying','reconciling')").fetchone()
            or store.db.execute("SELECT 1 FROM source_claims WHERE owner_kind='integration' AND state!='released'").fetchone())

    def check(self):
        descriptor = self.descriptor
        if self.closed or descriptor is None:
            raise StoreError('host_closed')
        self._physical(self.registration, self.pin_path)
        try:
            info = os.fstat(descriptor)
        except OSError as error:
            raise StoreError('host_closed') from error
        if self.closed or self.descriptor != descriptor:
            raise StoreError('host_closed')
        if (info.st_dev, info.st_ino) != self.registration.lock_identity or info.st_nlink != 1:
            raise StoreError('host_identity')

    def bind_path(self, path):
        self.check()
        path = Path(path).expanduser().resolve(strict=True)
        if str(path) != self.registration.database_path or _file(path) != self.registration.database_identity:
            raise StoreError('host_identity', 'Connection does not address the managed database')
        return path

    def validate_sql(self, store):
        if self.closed:
            raise StoreError('host_closed')
        row = store.db.execute('SELECT data FROM host_registry WHERE id=1').fetchone()
        current = store.db.execute('SELECT data FROM host_sessions WHERE id=1').fetchone()
        try:
            registration = HostRegistration.model_validate_json(row[0]) if row else None
            session = HostSession.model_validate_json(current[0]) if current else None
        except ValidationError as error:
            raise StoreError('host_ownership_unknown', 'Invalid host registration or session') from error
        if (str(store.path) != self.registration.database_path
                or store._host_database_identity != self.registration.database_identity
                or registration != self.registration
                or session is None or (session.id, session.generation, session.registration_id)
                    != (self.session.id, self.session.generation, self.registration.id)
                or session.phase not in {'recovering', 'ready'}):
            raise StoreError('host_fence')

    def connect(self, *, catalogue=None, clock=time.time):
        self.check()
        return Store(self.database, catalogue=catalogue, clock=clock, host=self)

    def _scope(self, store):
        projects = frozenset(row[0] for row in store.db.execute('SELECT project_id FROM workflows '
            'UNION SELECT project_id FROM verification_plans'))
        return AccessScope(actor_id='managed-host:' + self.session.id, project_ids=projects, role='runtime')

    def recover(self):
        from ..verification.control import VerificationControl

        store = self.connect()
        try:
            self.validate_sql(store)
            if self.session.phase == 'ready':
                return
            scope = self._scope(store)
            store.recover(scope)
            with store.transaction():
                self.validate_sql(store)
                VerificationControl(store)._revoke_all(scope, 'host_restart')
                self.session = changed(self.session, phase='ready', heartbeat_at=time.time())
                store.db.execute('UPDATE host_sessions SET data=? WHERE id=1', (self.session.model_dump_json(),))
        finally:
            store.close()

    def _release(self):
        self.closed = True
        self.descriptor = None
        if self._lock is not None:
            context, self._lock = self._lock, None
            context.__exit__(None, None, None)

    def drain(self):
        from ..verification.control import VerificationControl

        if self.closed or self.session.phase in {'draining', 'stopped'}:
            return
        try:
            store = self.connect()
            try:
                # Revocation and session closure share one writer transaction:
                # an admitted attempt is either observed here or denied afterwards.
                with store.transaction():
                    self.validate_sql(store)
                    scope = self._scope(store)
                    VerificationControl(store)._revoke_all(scope, 'host_close')
                    for workflow in store.workflows(scope):
                        for attempt in store.attempts(scope, workflow.id):
                            if attempt.state in {'assigned', 'running', 'stopping'}:
                                store._reconcile(scope, workflow, attempt, 'host_close')
                    phase = 'draining' if self._manual_busy(store) else 'stopped'
                    self.session = changed(self.session, phase=phase, heartbeat_at=time.time())
                    store.db.execute('UPDATE host_sessions SET data=? WHERE id=1', (self.session.model_dump_json(),))
            finally:
                store.close()
        except (OSError, ValueError):
            # Lost physical authority never grants resource settlement or repair.
            pass

    def close(self):
        try:
            self.drain()
        finally:
            self._release()
