"""Host-only source claims share the canonical store, never a second queue."""
import json
from contextlib import ExitStack, nullcontext
from pathlib import Path, PurePath
from uuid import uuid4

from ..domain.source import SourceClaim, SourceRoot
from ..verification.custody import locked, stopped
from .store import ACTIVE, StoreError, changed


def _overlap(left, right):
    a, b = PurePath(left.path), PurePath(right.path)
    return (a == b or a in b.parents or b in a.parents or left.identity == right.identity
        or left.identity in right.ancestors or right.identity in left.ancestors)


class Sources:
    def __init__(self, store):
        self.store = store

    def prepare(self, root: Path) -> SourceRoot:
        if self.store.db.in_transaction:
            raise RuntimeError('Source inspection cannot run in a SQLite transaction')
        path = Path(root).resolve(strict=True)
        if not path.is_dir():
            raise StoreError('source_unavailable')
        info = path.stat()
        ancestors = tuple((parent.st_dev, parent.st_ino) for parent in (item.stat() for item in path.parents))
        return SourceRoot(path=str(path), identity=(info.st_dev, info.st_ino), ancestors=ancestors)

    def _transaction(self):
        return nullcontext() if self.store.db.in_transaction else self.store.transaction()

    def get(self, identity) -> SourceClaim:
        return self.store._load('source_claims', identity, SourceClaim)

    def _save(self, claim):
        self.store.db.execute('UPDATE source_claims SET state=?,data=? WHERE id=?',
            (claim.state, claim.model_dump_json(), claim.id))
        return claim

    def acquire(self, root_ref, *, owner_kind, owner_id, coordinator_path, coordinator_identity,
                attempt_id=None, fence=None, quarantine=False) -> SourceClaim:
        if quarantine and owner_kind != 'legacy':
            raise StoreError('source_owner')
        claim = SourceClaim(id=uuid4().hex, root=root_ref, owner_kind=owner_kind, owner_id=owner_id,
            coordinator_path=str(coordinator_path), coordinator_identity=coordinator_identity,
            attempt_id=attempt_id, fence=fence, state='uncertain' if quarantine else 'held',
            reason='legacy_ownership_unknown' if quarantine else None)
        if not PurePath(claim.root.path).is_absolute() or not PurePath(claim.coordinator_path).is_absolute():
            raise StoreError('source_scope')
        if ((owner_kind == 'integration' and (attempt_id is None or fence is None))
                or (owner_kind == 'legacy' and (attempt_id is not None or fence is not None))):
            raise StoreError('source_owner')
        with self._transaction():
            row = self.store.db.execute('SELECT id FROM source_claims WHERE owner_kind=? AND owner_id=?',
                (owner_kind, owner_id)).fetchone()
            if row is not None:
                previous = self.get(row['id'])
                if previous.model_dump(exclude={'id', 'revision', 'state', 'reason', 'processes', 'external_effects'}) != claim.model_dump(
                        exclude={'id', 'revision', 'state', 'reason', 'processes', 'external_effects'}):
                    raise StoreError('source_owner')
                return previous
            if attempt_id is not None:
                attempt = self.store._attempt(attempt_id)
                task = self.store._task(attempt.task_id)
                if (attempt.fence != fence or attempt.state != 'running' or task.fence != fence
                        or task.current_attempt_id != attempt_id):
                    raise StoreError('fence')
            # Pre-upgrade coordinators have no claim/identity binding. A new
            # host cannot assume their missing resource record proves absence.
            if not quarantine and self.store.db.execute("SELECT 1 FROM integrations WHERE state IN ('preparing','verifying','applying','reconciling') "
                    "AND json_extract(data,'$.source_claim_id') IS NULL").fetchone():
                raise StoreError('source_busy')
            for row in (() if quarantine else self.store.db.execute("SELECT data FROM source_claims WHERE state!='released'")):
                if _overlap(claim.root, SourceClaim.model_validate_json(row['data']).root):
                    raise StoreError('source_busy')
            self.store.db.execute('INSERT INTO source_claims VALUES (?,?,?,?,?)',
                (claim.id, owner_kind, owner_id, claim.state, claim.model_dump_json()))
            return claim

    def guard(self, identity, owner_id) -> SourceClaim:
        claim = self.get(identity)
        if claim.owner_id != owner_id:
            raise StoreError('source_owner')
        if claim.state != 'held':
            raise StoreError('source_released' if claim.state == 'released' else 'effects_unknown')
        for row in self.store.db.execute("SELECT data FROM source_claims WHERE state='uncertain' AND id!=?", (identity,)):
            if _overlap(claim.root, SourceClaim.model_validate_json(row['data']).root):
                raise StoreError('effects_unknown')
        return claim

    def bind_process(self, identity, ref):
        with self._transaction():
            claim = self.get(identity)
            self.guard(identity, claim.owner_id)
            existing = next((value for value in claim.processes if value.id == ref.id), None)
            if existing is not None:
                if existing != ref:
                    raise StoreError('process_reference')
                return claim
            return self._save(changed(claim, processes=(*claim.processes, ref), revision=claim.revision + 1))

    def note_external(self, identity):
        with self._transaction():
            claim = self.get(identity)
            self.guard(identity, claim.owner_id)
            return self._save(changed(claim, external_effects=True, revision=claim.revision + 1))

    def mark_uncertain(self, identity, reason):
        with self._transaction():
            claim = self.get(identity)
            if claim.state == 'released':
                raise StoreError('source_released')
            return self._save(changed(claim, state='uncertain', reason=reason, revision=claim.revision + 1))

    def _terminal(self, claim):
        store = self.store
        if claim.owner_kind == 'legacy':
            busy = store.db.execute("SELECT 1 FROM model_calls WHERE attempt_id IS NULL AND state IN ('running','uncertain') "
                "AND json_extract(data,'$.owner_kind')='legacy' AND json_extract(data,'$.owner_id')=?", (claim.owner_id,)).fetchone()
        else:
            row = store.db.execute('SELECT data FROM integrations WHERE id=?', (claim.owner_id,)).fetchone()
            if row is None:
                raise StoreError('ownership_unknown')
            record = json.loads(row['data'])
            attempt = store._attempt(claim.attempt_id)
            if (record['attempt_id'] != claim.attempt_id or record['fence'] != claim.fence
                    or record.get('source_claim_id') != claim.id):
                raise StoreError('source_owner')
            if (record['state'] not in {'completed', 'invalidated', 'failed', 'conflict'}
                    or not attempt.stop_confirmed or attempt.state in (*ACTIVE, 'awaiting_review')):
                raise StoreError('resource_busy')
            busy = any(store.db.execute(f"SELECT 1 FROM {table} WHERE attempt_id=? AND state IN ('running','uncertain')",
                (claim.attempt_id,)).fetchone() for table in ('model_calls', 'tool_calls'))
        if busy:
            raise StoreError('resource_busy')

    def settle(self, identity) -> SourceClaim:
        if self.store.db.in_transaction:
            raise RuntimeError('Source settlement cannot run in a SQLite transaction')
        claim = self.get(identity)
        if claim.state == 'released':
            return claim
        if claim.state == 'uncertain' or claim.external_effects:
            raise StoreError('effects_unknown')
        with ExitStack() as locks:
            try:
                locks.enter_context(locked(Path(claim.coordinator_path), identity=claim.coordinator_identity))
            except BlockingIOError as error:
                raise StoreError('coordinator_running') from error
            except (OSError, ValueError) as error:
                raise StoreError('ownership_unknown') from error
            for ref in claim.processes:
                try:
                    locks.enter_context(stopped(ref))
                except (OSError, ValueError) as error:
                    raise StoreError('process_unconfirmed') from error
            with self.store.transaction():
                if self.get(identity) != claim:
                    raise StoreError('revision')
                self._terminal(claim)
                return self._save(changed(claim, state='released', revision=claim.revision + 1))
