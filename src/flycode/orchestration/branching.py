"""Validate additive owner grants before allocating canonical branch identities."""

from pathlib import Path
from uuid import uuid4

from ..domain.orchestration import WorkerBinding
from ..domain.pipeline import PipelineBranch
from ..verification.plan import contract_hash
from .integration import registered_source
from .integration_bindings import record_hash
from .store import StoreError, changed, covers, structural_aggregate
from .tools import SUPPORTED


class Branching:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)

    def prepare(self, scope, recipe, tasks, attempts, additions, oracles):
        store = self.store
        if not additions:
            return (), {}, {}
        if (len(tasks) + 2 * len(additions) > recipe.workflow_budget.max_tasks
                or len(recipe.branches) + len(additions) > 31):
            raise StoreError('task_limit')
        children = {branch.writer_id for branch in recipe.branches if branch.writer_id != recipe.parent_id}
        references = children | {f'new:{number}' for number in range(len(additions))}
        parents = {recipe.parent_id, *(task.id for task in tasks.values() if structural_aggregate(task, tasks.values(), attempts))}
        profiles, templates, prepared = {}, {}, []
        for addition in additions:
            if addition.template_writer_id not in {branch.writer_id for branch in recipe.branches}:
                raise StoreError('branch_template')
            row = store.db.execute('SELECT data FROM worker_bindings WHERE task_id=? AND current=1', (addition.template_writer_id,)).fetchone()
            if row is None:
                raise StoreError('branch_template')
            template = WorkerBinding.model_validate_json(row['data'])
            if (template.workflow_id != recipe.workflow_id or template.project_id != recipe.project_id
                    or template.runtime_root != str(self.root) or template.source_root != recipe.source_root
                    or template.source_hash != recipe.source_hash):
                raise StoreError('branch_template')
            templates[addition.template_writer_id] = template
            drafts = []
            for draft, role in ((addition.writer, 'developer'), (addition.reviewer, 'reviewer')):
                permission, budget = draft.permissions, draft.budget
                if (draft.kind != ('implementation' if role == 'developer' else 'review') or not draft.objective.strip()
                        or draft.acceptance is None or permission is None or not permission.approved
                        or permission.read_scopes != ('.',) or budget is None or not budget.tool_calls
                        or (role == 'developer' and not budget.model_calls)
                        or budget.model_calls > recipe.workflow_budget.max_model_calls or budget.tool_calls > recipe.workflow_budget.max_tool_calls
                        or (draft.parent_id is not None and draft.parent_id not in parents)
                        or draft.input_artifacts or draft.base_snapshot_hash not in {None, recipe.source_hash}
                        or not draft.allowed_profile_ids):
                    raise StoreError('branch_scope')
                if (not permission.tools or set(permission.tools) - (SUPPORTED | {'stop'})
                        or (role == 'reviewer' and (permission.write_scopes or set(permission.tools) != {'run_tests'} or draft.dependencies))
                        or not set(draft.dependencies) <= references):
                    raise StoreError('unsupported_profile')
                for identity in draft.allowed_profile_ids:
                    profile = store._profile(identity)
                    if (profile.role != role or recipe.project_id not in profile.project_ids or not profile.permissions.approved
                            or not set(draft.capabilities) <= set(profile.capabilities)
                            or not set(permission.tools) <= set(profile.permissions.tools)
                            or not covers(profile.permissions.read_scopes, permission.read_scopes)
                            or not covers(profile.permissions.write_scopes, permission.write_scopes)):
                        raise StoreError('profile_scope')
                    profiles[identity] = profile
                drafts.append(changed(draft, parent_id=draft.parent_id or recipe.parent_id))
            if contract_hash(addition.writer.acceptance) != contract_hash(addition.reviewer.acceptance):
                raise StoreError('branch_criteria')
            prepared.append(changed(addition, writer=drafts[0], reviewer=drafts[1]))
        # Model admission and physical project/oracle inspection must stay outside SQL.
        for profile in profiles.values():
            if not store._binding_current(profile):
                raise StoreError('model_unavailable')
        registered_source(store, recipe.project_id, recipe.source_root)
        for addition in prepared:
            oracles.resolve(addition.approval_hash, addition.writer.acceptance)
            oracles.resolve(addition.approval_hash, addition.reviewer.acceptance)
        return tuple(prepared), profiles, templates

    def current(self, profiles, templates):
        for identity, profile in profiles.items():
            if self.store._profile(identity) != profile:
                raise StoreError('profile_scope')
        for identity, template in templates.items():
            row = self.store.db.execute('SELECT data FROM worker_bindings WHERE task_id=? AND current=1', (identity,)).fetchone()
            if row is None or WorkerBinding.model_validate_json(row['data']) != template:
                raise StoreError('branch_template')

    def allocate(self, scope, recipe, workflow, additions, templates):
        store = self.store
        if not store.db.in_transaction:
            raise RuntimeError('Branch identities require an owner transaction')
        pairs = [(store._new_task(workflow, item.writer), store._new_task(workflow, item.reviewer)) for item in additions]
        aliases = {f'new:{number}': writer.id for number, (writer, _) in enumerate(pairs)}
        tasks, bindings, approvals, branches = {}, {}, {}, []
        for addition, (writer, reviewer) in zip(additions, pairs):
            writer = changed(writer, dependencies=tuple(aliases.get(identity, identity) for identity in writer.dependencies),
                state='planned' if writer.dependencies else 'ready', queued_at=store.clock())
            template = templates[addition.template_writer_id]
            binding = changed(template, id=uuid4().hex, task_id=writer.id, revision=1, previous_id=None,
                semantic_revision=writer.semantic_revision, contract_hash=writer.contract_hash, owner_id=scope.actor_id,
                created_at=store.clock())
            tasks.update({writer.id: writer, reviewer.id: reviewer})
            bindings[writer.id] = binding
            approvals.update({writer.id: addition.approval_hash, reviewer.id: addition.approval_hash})
            branches.append(PipelineBranch(writer_id=writer.id, reviewer_id=reviewer.id,
                worker_binding_id=binding.id, worker_binding_hash=record_hash(binding)))
        return tasks, bindings, approvals, tuple(branches), aliases
