"""Owner-only registration of acceptance contracts and immutable oracle bytes."""

import hashlib
import json
import re
import shutil
import stat
from pathlib import Path
from uuid import uuid4

from ..domain.verification import (
    AcceptanceContract,
    CheckSpec,
    VerificationModel,
    VerificationPlan,
)
from .evidence import atomic_json
from .plan import PlanBuilder, contract_hash, registered_checks
from .snapshot import digest, input_files, write_files


class ApprovedOracle(VerificationModel):
    contract: AcceptanceContract
    checks: list[CheckSpec]
    files: dict[str, str]
    modes: dict[str, int]

    def fingerprint(self) -> str:
        return digest({'contract': contract_hash(self.contract),
            'checks': [check.model_dump(exclude={'subject_hash'}) for check in self.checks],
            'files': self.files, 'modes': self.modes})


class OracleRegistry:
    def __init__(self, directory: Path, *, directory_fd: int | None = None):
        self.directory = directory.absolute() if directory_fd is not None else directory.resolve()
        self.directory_fd = directory_fd
        if directory_fd is None:
            self.directory.mkdir(parents=True, exist_ok=True)

    def approve(self, contract: AcceptanceContract, checks: list[CheckSpec], root: Path, *,
                expected_hash: str | None = None) -> str:
        """Trusted registration only; deliberately absent from agent tools/API commands."""
        registered = registered_checks(checks, contract)
        files, _ = input_files(root.resolve(strict=True))
        if not files:
            raise ValueError('Approval requires trusted oracle files')
        for check in registered:
            if check.required and any(target.split('::', 1)[0] not in files for target in check.targets):
                raise ValueError('Mandatory target is not in the trusted oracle')
        approved = ApprovedOracle(contract=contract, checks=registered,
            files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            modes={name: stat.S_IMODE((root / name).stat().st_mode) for name in files})
        key = approved.fingerprint()
        if expected_hash is not None and key != expected_hash:
            raise ValueError('Imported approval differs from the expected approval')
        if self.directory_fd is not None:
            payloads = {f'{key}/files/{name}': data for name, data in files.items()}
            payloads[f'{key}/approval.json'] = approved.model_dump_json().encode()
            payloads['heads.json'] = json.dumps({contract.id: key}, sort_keys=True).encode()
            modes = {f'{key}/files/{name}': mode for name, mode in approved.modes.items()}
            modes.update({f'{key}/approval.json': 0o600, 'heads.json': 0o600})
            write_files(self.directory_fd, payloads, modes)
            return key
        if (self.directory / key).exists():
            self.read(key)
            self._activate(key, approved)
            return key
        temporary = self.directory / f'.{uuid4().hex}'
        temporary.mkdir()
        try:
            for name, data in files.items():
                target = temporary / 'files' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(approved.modes[name])
            atomic_json(temporary / 'approval.json', approved.model_dump_json())
            temporary.rename(self.directory / key)
            self._activate(key, approved)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return key

    def _heads(self) -> dict[str, str]:
        path = self.directory / 'heads.json'
        heads = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(heads, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                              for key, value in heads.items()):
            raise ValueError('Invalid approval heads')
        return heads

    def _activate(self, key: str, approved: ApprovedOracle) -> None:
        heads = self._heads()
        previous = heads.get(approved.contract.id)
        if previous is not None:
            old = self.read(previous).contract
            if old.version > approved.contract.version or (old.version == approved.contract.version
                    and contract_hash(old) != contract_hash(approved.contract)):
                raise ValueError('Semantic contract changes require a newer owner version')
        heads[approved.contract.id] = key
        atomic_json(self.directory / 'heads.json', json.dumps(heads, sort_keys=True))

    def read(self, key: str) -> ApprovedOracle:
        if not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('Invalid approval ID')
        directory = self.directory / key
        try:
            if directory.is_symlink():
                raise ValueError('Symlink approval is forbidden')
            approved = ApprovedOracle.model_validate_json((directory / 'approval.json').read_text())
            if approved.fingerprint() != key:
                raise ValueError('Approval contract changed')
            files, _ = input_files(directory / 'files')
            hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
            modes = {name: stat.S_IMODE((directory / 'files' / name).stat().st_mode) for name in files}
            if hashes != approved.files or modes != approved.modes:
                raise ValueError('Trusted oracle bytes changed')
            return approved
        except OSError as exc:
            raise ValueError('Approved oracle is unavailable') from exc

    def resolve(self, key: str, contract: AcceptanceContract) -> ApprovedOracle:
        approved = self.read(key)
        if self._heads().get(approved.contract.id) != key:
            raise ValueError('A newer owner approval superseded this plan')
        if contract_hash(contract) != contract_hash(approved.contract):
            raise ValueError('Acceptance contract changed')
        return approved

    def validate(self, plan: VerificationPlan) -> ApprovedOracle:
        if plan.scope != 'acceptance-contract' or plan.acceptance is None or plan.approval_hash is None:
            raise ValueError('An owner-approved acceptance plan is required')
        approved = self.resolve(plan.approval_hash, plan.acceptance)
        if plan.contract_hash != contract_hash(approved.contract):
            raise ValueError('Acceptance contract changed')
        by_id = {check.id: check for check in plan.checks}
        known = {check.id for check in approved.checks}
        for expected in approved.checks:
            actual = by_id.get(expected.id)
            if actual is None or actual.model_dump(exclude={'subject_hash'}) != expected.model_dump(exclude={'subject_hash'}):
                raise ValueError('Approved checks cannot be deleted or changed')
        if any(check.required for check in plan.checks if check.id not in known):
            raise ValueError('Agent additions must be optional')
        if 'integration' not in plan.subjects:
            raise ValueError('Integrated subject is missing')
        rebuilt = PlanBuilder().build(plan.subjects['integration'], plan.acceptance, plan.checks,
            subjects=plan.subjects, approval_hash=plan.approval_hash, reruns=plan.reruns)
        if rebuilt.checks != plan.checks or rebuilt.subject_hash != plan.subject_hash:
            raise ValueError('Plan subject bindings changed')
        return approved

    def overlay(self, key: str, destination: Path) -> None:
        approved = self.read(key)
        for name in approved.files:
            target = destination / name
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Oracle target escapes sandbox')
            target.parent.mkdir(parents=True, exist_ok=True)
            data = (self.directory / key / 'files' / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != approved.files[name]:
                raise ValueError('Oracle changed during copy')
            target.write_bytes(data)
            target.chmod(approved.modes[name])
