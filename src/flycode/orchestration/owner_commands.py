"""Explicit local owner intent and replay on the elected Host connection."""

from pathlib import Path

from ..brain import BRAIN_REGISTRY
from ..domain.orchestration import AccessScope, QueueModel
from ..domain.pipeline import BranchAddition
from ..domain.project import CanonicalProject
from ..domain.source import SourceRoot
from ..domain.verification_control import RegisteredVerificationProfile
from ..verification.oracle_transfer import OracleTransfer
from ..verification.registry import VerificationRegistry
from ..verification.snapshot import digest
from .dispatch import Dispatcher
from .owner_requests import (
    OwnerApprove,
    OwnerCommand,
    OwnerOutcome,
    OwnerPrepare,
    OwnerPreview,
    OwnerProfile,
    OwnerReplan,
    OwnerResult,
    OwnerTask,
    owner_request,
)
from .pipeline import Pipelines
from .projects import Projects
from .store import StoreError

ACTOR = 'cli:local-owner-v1'


class OwnerIntent(QueueModel):
    command: OwnerCommand
    project: CanonicalProject
    runtime: SourceRoot
    profiles: dict[str, RegisteredVerificationProfile]
    additions: tuple[BranchAddition, ...] = ()


def _key(command, stage):
    return 'owner-' + stage + '/' + digest({'key': command.idempotency_key})


class OwnerCommands:
    def __init__(self, store):
        if store.host is None:
            raise StoreError('host_required')
        self.store = store
        self.runtime = store.host.registration.runtime_root
        self.root = Path(self.runtime.path)

    def execute(self, value):
        try:
            command = owner_request(value)
        except ValueError:
            return OwnerOutcome(error_code='invalid_fields')
        try:
            self.store._host_guard()
            project = self._project(command)
            scope = AccessScope(actor_id=ACTOR, project_ids={project.id}, role='owner')
            if isinstance(command, OwnerPreview):
                return OwnerOutcome(result=self._preview(scope, project, command.workflow_id))
            if isinstance(command, OwnerPrepare):
                self.dispatcher = Dispatcher(self.store, self.root)
            intent = self._intent(scope, project, command)
            Projects(self.store)._grant(project, ACTOR)
            if isinstance(intent.command, OwnerPrepare):
                return self._prepare(scope, intent)
            return OwnerOutcome(result=self._approve(scope, intent))
        except StoreError as error:
            return OwnerOutcome(error_code=error.code)
        except (OSError, ValueError):
            return OwnerOutcome(error_code='owner_input_changed')

    def _project(self, command):
        projects = Projects(self.store)
        saved = projects.get(command.project_id)
        current = projects.resolve(Path(saved.root.path), actor_id=None)
        if current != saved or current.revision != command.project_revision:
            raise StoreError('project_changed')
        source = Path(current.root.path)
        if self.root.is_relative_to(source) or source.is_relative_to(self.root):
            raise StoreError('workspace_location')
        return current

    def _workflow(self, scope, command):
        identity = (self._pipeline(scope, command.pipeline_id).workflow_id
            if isinstance(command, OwnerReplan) else command.workflow_id)
        workflow = self.store.workflow(scope, identity)
        if workflow.revision != command.expected_revision:
            raise StoreError('revision')
        return workflow

    def _pipeline(self, scope, identity):
        row = self.store.db.execute('SELECT data FROM pipelines WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise StoreError('not_found')
        recipe = Pipelines.effective(self.store, row['data'])
        workflow = self.store.workflow(scope, recipe.workflow_id)
        if recipe.runtime_root != str(self.root) or recipe.project_id != workflow.project_id:
            raise StoreError('pipeline_scope')
        return recipe

    def _intent(self, scope, project, command):
        store, registry = self.store, VerificationRegistry(self.store)
        request = {'owner_command_v1': command.model_dump(mode='json')}
        key = _key(command, 'intent')
        with store.transaction():
            found, result, _ = store._replay(scope, key, request)
        if found:
            saved = OwnerIntent.model_validate(result)
            if saved.project != project or saved.runtime != self.runtime:
                raise StoreError('project_changed')
            return saved
        workflow = self._workflow(scope, command)
        profiles, additions = {}, []
        if isinstance(command, OwnerPrepare):
            for identity, worker in command.workers.items():
                task, _ = self.dispatcher._can_bind(scope, identity, worker.expected_revision, worker.expected_binding_id)
                if task.workflow_id != workflow.id:
                    raise StoreError('scope')
                if worker.brain not in BRAIN_REGISTRY:
                    raise StoreError('brain')
        else:
            if set(command.task_revisions) != {task.id for task in store.tasks(scope, workflow.id)}:
                raise StoreError('revision')
            for task_id, revision in command.task_revisions.items():
                if store.task(scope, task_id).revision != revision:
                    raise StoreError('revision')
            if set(command.profiles) != set(command.task_revisions):
                raise StoreError('verification_profile_scope')
            refs = dict(command.profiles)
            if isinstance(command, OwnerReplan):
                refs.update({f'new:{number}': item.profile for number, item in enumerate(command.new_branches)})
            for identity, ref in refs.items():
                profile = registry.profile(scope, ref.id)
                if (profile.project_id != project.id or profile.version != ref.version
                        or profile.draft.acceptance is None or profile.approval_hash is None):
                    raise StoreError('verification_profile_scope')
                profiles[identity] = profile
            if isinstance(command, OwnerReplan):
                for number, item in enumerate(command.new_branches):
                    additions.append(BranchAddition(writer=item.writer, reviewer=item.reviewer,
                        template_writer_id=item.template_writer_id, approval_hash=profiles[f'new:{number}'].approval_hash))
        intent = OwnerIntent(command=command, project=project, runtime=self.runtime, profiles=profiles, additions=tuple(additions))
        store._host_guard()
        with store.transaction():
            found, result, request_hash = store._replay(scope, key, request)
            if found:
                return OwnerIntent.model_validate(result)
            store._host_sql()
            registry._project_sql(project)
            if self._workflow(scope, command) != workflow:
                raise StoreError('revision')
            for profile in profiles.values():
                registry._profile_sql(profile)
            store._remember(scope, key, project.id, request_hash, intent)
        return intent

    def _prepare(self, scope, intent):
        command, bindings = intent.command, {}
        assert isinstance(command, OwnerPrepare)
        failure = None
        for identity, worker in command.workers.items():
            try:
                binding = self.dispatcher.bind(scope, identity, Path(intent.project.root.path),
                    expected_revision=worker.expected_revision, expected_binding_id=worker.expected_binding_id,
                    brain=worker.brain, seed=worker.seed, key=_key(command, 'bind-' + identity), project_pin=intent.project,
                    expected_workflow_revision=command.expected_revision)
                bindings[identity] = binding.id
            except StoreError as error:
                failure = error.code
                break
            except (OSError, ValueError):
                failure = 'owner_input_changed'
                break
        return OwnerOutcome(error_code=failure, result=OwnerResult(kind='prepared', project_id=intent.project.id,
            project_revision=intent.project.revision, workflow_id=command.workflow_id,
            workflow_revision=command.expected_revision, binding_ids=bindings, partial=failure is not None))

    def _approve(self, scope, intent):
        command = intent.command
        key = _key(command, 'recipe')
        exists = self.store.db.execute('SELECT 1 FROM commands WHERE actor_id=? AND key=?', (ACTOR, key)).fetchone()
        if exists is None:
            transfer = OracleTransfer(self.store)
            for profile in {value.id: value for value in intent.profiles.values()}.values():
                transfer.install(scope, profile)
        service = Pipelines(self.store, self.root)
        if isinstance(command, OwnerApprove):
            recipe = service.approve_parent(scope, command.workflow_id, command.pairs, command.integrator_id,
                source=Path(intent.project.root.path), expected_revision=command.expected_revision,
                expected_revisions=command.task_revisions,
                approval_hashes={identity: profile.approval_hash for identity, profile in intent.profiles.items()},
                preparation_steps=command.preparation_steps, reruns=command.reruns,
                retry_policies=command.retry_policies, profile_pins=intent.profiles, key=key)
            kind = 'approved'
        else:
            assert isinstance(command, OwnerReplan)
            recipe = service.replan(scope, command.pipeline_id,
                changes={identity: change.model_dump(exclude_none=True) for identity, change in command.changes.items()},
                expected_revision=command.expected_revision, expected_revisions=command.task_revisions,
                new_branches=intent.additions, profile_pins=intent.profiles, key=key)
            kind = 'replanned'
        return OwnerResult(kind=kind, project_id=intent.project.id, project_revision=intent.project.revision,
            workflow_id=recipe.workflow_id, workflow_revision=command.expected_revision + 1,
            pipeline_id=recipe.id, graph_revision=recipe.graph_revision,
            added_task_ids=tuple(sorted(set(recipe.tasks) - set(command.task_revisions))))

    def _preview(self, scope, project, workflow_id):
        store = self.store
        with store.transaction():
            VerificationRegistry(store)._project_sql(project)
            workflow = store.workflow(scope, workflow_id)
            tasks = store.tasks(scope, workflow_id)
            rows = store.db.execute('SELECT data FROM verification_profiles WHERE project_id=? ORDER BY rowid LIMIT 65', (project.id,)).fetchall()
            bindings = {row['task_id']: row['id'] for row in store.db.execute(
                'SELECT b.id,b.task_id FROM worker_bindings b JOIN tasks t ON b.task_id=t.id WHERE t.workflow_id=? AND b.current=1', (workflow_id,))}
            pipeline = store.db.execute('SELECT id FROM pipelines WHERE workflow_id=?', (workflow_id,)).fetchone()
            recipe = self._pipeline(scope, pipeline['id']) if pipeline else None
        if len(rows) > 64 or len(tasks) > 64:
            raise StoreError('task_limit')
        profiles = []
        registry = VerificationRegistry(store)
        for row in rows:
            record = RegisteredVerificationProfile.model_validate_json(row['data'])
            reason = None
            try:
                registry.profile(scope, record.id)
            except StoreError as error:
                reason = error.code
            profiles.append(OwnerProfile(id=record.id, version=record.version, title=record.draft.title,
                criteria_id=record.draft.acceptance.id if record.draft.acceptance else None,
                criteria_version=record.draft.acceptance.version if record.draft.acceptance else None,
                reruns=record.draft.reruns, available=reason is None, reason=reason))
        cards = []
        for task in tasks:
            blockers = []
            for missing, code in ((not task.objective, 'missing_objective'), (task.acceptance is None, 'missing_criteria'),
                    (task.permissions is None or not task.permissions.approved, 'missing_permission'),
                    (task.budget is None, 'missing_budget'), (not task.allowed_profile_ids, 'missing_agent_profile'),
                    (task.kind != 'review' and bool(task.allowed_profile_ids) and task.id not in bindings, 'missing_source_binding')):
                if missing:
                    blockers.append(code)
            if task.blocked_reason:
                blockers.append(task.blocked_reason)
            cards.append(OwnerTask(id=task.id, title=task.title, kind=task.kind, revision=task.revision,
                state=task.state, parent_id=task.parent_id, dependencies=task.dependencies, profile_ids=task.allowed_profile_ids,
                criteria_id=task.acceptance.id if task.acceptance else None, criteria_version=task.acceptance.version if task.acceptance else None,
                permission_approved=task.permissions is not None and task.permissions.approved, budget=task.budget,
                binding_id=bindings.get(task.id), blockers=tuple(blockers)))
        return OwnerResult(kind='preview', project_id=project.id, project_revision=project.revision,
            workflow_id=workflow.id, workflow_revision=workflow.revision,
            pipeline_id=recipe.id if recipe else None, graph_revision=recipe.graph_revision if recipe and recipe.version == 4 else 0,
            tasks=tuple(cards), profiles=tuple(profiles))
