"""Durable, explicitly probed capabilities in the canonical host database."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.orchestration import HostModelCall
from ..llm.catalogue import ModelDescriptor, ModelRole
from .store import Store, StoreError


class ProbeEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')

    call_id: str
    owner_id: str
    configuration_sha256: str


class ModelAdmission(BaseModel):
    model_config = ConfigDict(extra='forbid')

    version: Literal['model-admission-v1'] = 'model-admission-v1'
    descriptor: ModelDescriptor
    proofs: dict[ModelRole, ProbeEvidence] = Field(default_factory=dict)


def configuration(entry: ModelDescriptor) -> tuple:
    return (entry.model_ref, entry.model_id, entry.provider, entry.endpoint_ref, entry.capability_version,
            entry.weights_revision, entry.loaded_instance_id, entry.context_limit, entry.load_fingerprint)


def configuration_hash(entry: ModelDescriptor) -> str:
    return hashlib.sha256(json.dumps(configuration(entry)).encode()).hexdigest()


class ModelAdmissions:
    def __init__(self, database: Path):
        self.database = Path(database)
        Store(self.database).close()

    def _validate(self, db: sqlite3.Connection, admission: ModelAdmission) -> None:
        entry = admission.descriptor
        if set(entry.roles) != set(admission.proofs) or len(entry.roles) != len(admission.proofs):
            raise StoreError('catalogue_evidence', 'Role receipts do not match the descriptor')
        if entry.roles and (entry.status != 'ready' or entry.capability_source != 'explicit-local-probe'
                            or not entry.loaded_instance_id or not entry.context_limit or not entry.load_fingerprint):
            raise StoreError('catalogue_evidence', 'Missing explicit loaded-model capability')
        for role, evidence in admission.proofs.items():
            row = db.execute('SELECT * FROM model_calls WHERE id=?', (evidence.call_id,)).fetchone()
            if row is None or row['attempt_id'] is not None or row['state'] != 'completed':
                raise StoreError('catalogue_evidence', 'Probe receipt is not completed')
            call = HostModelCall.model_validate_json(row['data'])
            if (configuration_hash(entry) != evidence.configuration_sha256
                    or call.id != evidence.call_id or call.owner_id != evidence.owner_id
                    or call.owner_kind != 'probe' or call.status != 'completed' or call.late
                    or call.ended_at is None or call.endpoint_ref != entry.endpoint_ref
                    or row['endpoint_ref'] != entry.endpoint_ref or call.model_id != entry.loaded_instance_id
                    or call.role != role):
                raise StoreError('catalogue_evidence', 'Probe receipt does not cover this role and model')

    def load(self, endpoint_ref: str) -> dict[str, ModelAdmission]:
        db = sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN')
            result = {}
            for row in db.execute('SELECT * FROM model_admissions WHERE endpoint_ref=?', (endpoint_ref,)):
                admission = ModelAdmission.model_validate_json(row['data'])
                if (admission.descriptor.model_ref != row['model_ref']
                        or admission.descriptor.endpoint_ref != endpoint_ref):
                    raise StoreError('catalogue_evidence', 'Descriptor identity mismatch')
                self._validate(db, admission)
                result[row['model_ref']] = admission
            return result
        finally:
            db.close()

    def save(self, entries: dict[str, ModelDescriptor], previous: dict[str, ModelAdmission], *,
             proof: tuple[str, ModelRole, str, str] | None = None) -> None:
        store = Store(self.database)
        try:
            with store.transaction():
                for ref, entry in entries.items():
                    prior = previous.get(ref)
                    row = store.db.execute('SELECT data FROM model_admissions WHERE model_ref=?', (ref,)).fetchone()
                    actual = ModelAdmission.model_validate_json(row['data']) if row else None
                    if actual != prior:
                        raise StoreError('catalogue_conflict', 'Model catalogue changed; refresh before retrying')
                    proofs = ({role: identity for role, identity in prior.proofs.items() if role in entry.roles}
                              if prior and configuration(prior.descriptor) == configuration(entry) else {})
                    if proof and proof[0] == ref:
                        proofs[proof[1]] = ProbeEvidence(call_id=proof[2], owner_id=proof[3],
                                                       configuration_sha256=configuration_hash(entry))
                    admission = ModelAdmission(descriptor=entry, proofs=proofs)
                    self._validate(store.db, admission)
                    if admission != prior:
                        store.db.execute('INSERT INTO model_admissions VALUES (?,?,?) '
                            'ON CONFLICT(model_ref) DO UPDATE SET endpoint_ref=excluded.endpoint_ref,data=excluded.data',
                            (ref, entry.endpoint_ref, admission.model_dump_json()))
        finally:
            store.close()
