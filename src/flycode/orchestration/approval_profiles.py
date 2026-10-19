"""Exact registered owner criteria shared by recipe preflight and SQL publication."""

from pydantic import TypeAdapter

from ..domain.verification_control import RegisteredVerificationProfile
from ..verification.plan import contract_hash
from ..verification.registry import VerificationRegistry
from ..verification.snapshot import digest
from .store import StoreError


class ApprovalProfiles:
    def __init__(self, store, profiles):
        self.registry = VerificationRegistry(store)
        self.profiles = TypeAdapter(dict[str, RegisteredVerificationProfile]).validate_python(profiles) if profiles is not None else None
        self.project = None
        if self.profiles is not None and len(self.profiles) > 64:
            raise StoreError('task_limit')

    def request(self):
        return {identity: {'id': profile.id, 'hash': digest(profile.model_dump(mode='json'))}
            for identity, profile in self.profiles.items()} if self.profiles is not None else None

    def prepare(self, scope, project_id, contracts, approvals, reruns):
        if self.profiles is None:
            return
        if self.registry.store.db.in_transaction:
            raise RuntimeError('Registered profile inspection cannot hold a SQL transaction')
        if set(self.profiles) != set(contracts) or set(approvals) != set(contracts):
            raise StoreError('verification_profile_scope')
        self.project = self.registry._project(scope, project_id, owner=True)
        for identity, profile in self.profiles.items():
            if (profile.project_id != project_id or profile.draft.acceptance is None or contracts[identity] is None
                    or profile.approval_hash != approvals[identity]
                    or contract_hash(profile.draft.acceptance) != contract_hash(contracts[identity])
                    or profile.draft.trust != 'trusted-local-process' or profile.draft.reruns != reruns):
                raise StoreError('verification_profile_scope')
            if self.registry.profile(scope, profile.id) != profile:
                raise StoreError('verification_profile_changed')

    def current(self):
        if self.profiles is not None:
            assert self.project is not None
            self.registry._project_sql(self.project)
            for profile in self.profiles.values():
                self.registry._profile_sql(profile)
