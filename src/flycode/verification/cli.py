"""CLI adapter for the registered local pytest verification profile."""

import json
import signal
import sqlite3
import sys
from pathlib import Path
from threading import Event, current_thread, main_thread

from ..domain.orchestration import AccessScope
from ..domain.verification_control import (
    VerificationCommand,
    VerificationPlanDraft,
    VerificationProfileDraft,
)
from ..orchestration.attach import attach_coordinator
from ..orchestration.projects import Projects
from ..orchestration.store import Store, StoreError, default_state_path
from ..tracker.cli import ACTOR, _identity, _json
from .control import VerificationControl
from .engine import VerificationEngine
from .projection import VerificationViews, command_receipt
from .registry import VerificationRegistry


def configure_parser(subparsers):
    parser = subparsers.add_parser('verification', help='Register and inspect shared verification plans')
    parser.add_argument('--database', help='Canonical state database')
    commands = parser.add_subparsers(dest='verification_action', required=True)
    for action in ('register-subject', 'register-profile', 'create', 'list', 'show', 'events', 'stops'):
        command = commands.add_parser(action)
        if action != 'stops':
            command.add_argument('--project', required=True, help='Explicit local project folder')
        if action == 'register-subject':
            origin = command.add_mutually_exclusive_group()
            origin.add_argument('--workspace-attempt', type=_identity, help='Capture an existing owned workspace baseline')
            origin.add_argument('--artifact', type=_identity, help='Capture a pinned patch artifact')
            origin.add_argument('--integration', type=_identity, help='Capture a pinned integration stage')
            command.add_argument('--stage', choices=['baseline', 'target', 'regression', 'integration'])
            command.add_argument('--checked-task', type=_identity, help='Required task ID for an integration stage')
        if action in {'register-profile', 'create'}:
            command.add_argument('--input', required=True, help='Registered profile or plan JSON file')
        if action == 'register-profile':
            command.add_argument('--oracle-root', help='Explicit owner-approved oracle folder')
        if action in {'show', 'events'}:
            command.add_argument('plan_id', type=_identity)
        if action in {'list', 'events', 'stops'}:
            command.add_argument('--after', type=int, default=0)
            command.add_argument('--limit', type=int, default=50)


def catalogue_command(args) -> int:
    store = None
    try:
        store = Store(Path(args.database).expanduser() if args.database else default_state_path())
        views, registry, action = VerificationViews(store), VerificationRegistry(store), args.verification_action
        if action == 'stops':
            result = views.stops(ACTOR, after=args.after, limit=args.limit)
        else:
            projects = Projects(store)
            project = (projects.register if action == 'register-subject' else projects.resolve)(
                Path(args.project).expanduser(), actor_id=ACTOR)
            scope = AccessScope(actor_id=ACTOR, project_ids=frozenset({project.id}), role='owner')
            if action == 'register-subject':
                if args.checked_task and not args.integration or args.stage and not (args.artifact or args.integration):
                    raise StoreError('command_mode')
                if args.workspace_attempt:
                    subject = registry.register_workspace_subject(scope, args.workspace_attempt)
                elif args.artifact:
                    subject = registry.register_artifact_subject(scope, args.artifact, stage=args.stage or 'target')
                elif args.integration:
                    if args.checked_task is None:
                        raise StoreError('command_fields_required')
                    subject = registry.register_integration_subject(scope, args.integration,
                        task_id=args.checked_task, stage=args.stage or 'integration')
                else:
                    subject = registry.register_subject(scope, project.id)
                result = {'id': subject.id, 'project_id': project.id, 'subject_hash': subject.snapshot.subject_hash}
            elif action == 'register-profile':
                profile = registry.register_profile(scope, project.id, VerificationProfileDraft.model_validate(_json(args.input)),
                    oracle_root=Path(args.oracle_root).expanduser() if args.oracle_root else None)
                result = {'id': profile.id, 'project_id': project.id, 'version': profile.version, 'title': profile.draft.title}
            elif action == 'create':
                record = registry.create_plan(scope, VerificationPlanDraft.model_validate(_json(args.input)))
                result = views.plan(scope, record.id)
            elif action == 'show':
                result = views.plan(scope, args.plan_id)
            elif action == 'list':
                result = views.page(scope, after=args.after, limit=args.limit)
            else:
                events = VerificationControl(store).events(scope, args.plan_id, after=args.after, limit=args.limit)
                result = {'items': [event.model_dump(mode='json') for event in events],
                    'cursor': events[-1].sequence if events else args.after}
        print(json.dumps(result if isinstance(result, dict) else result.model_dump(mode='json'),
            ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        return _error(error)
    finally:
        if store is not None:
            store.close()


def _error(error):
    code = error.code if isinstance(error, StoreError) else 'local_unavailable' if isinstance(error, OSError) else 'invalid_fields'
    detail = {'code': code}
    if isinstance(error, StoreError) and error.code == 'revision' and error.current is not None:
        detail['revision'] = error.current['revision']
    print(json.dumps({'error': detail}), file=sys.stderr)
    return 2


def _registered_command(args):
    store = None
    try:
        if args.expected_revision is None or not args.key:
            raise StoreError('command_fields_required')
        if args.evidence_id and (not args.reproduce or args.operation != 'start'):
            raise StoreError('reproduction_required')
        if args.reproduce and not args.evidence_id:
            raise StoreError('reproduction_required')
        database = Path(args.database).expanduser() if args.database else default_state_path()
        store = Store(database)
        views = VerificationViews(store)
        if args.operation == 'cancel':
            result = views.cancel(ACTOR, args.plan_id, VerificationCommand(operation='cancel',
                expected_revision=args.expected_revision, idempotency_key=args.key))
        else:
            if not args.project:
                raise StoreError('project_required')
            project = Projects(store).resolve(Path(args.project).expanduser(), actor_id=ACTOR)
            scope = AccessScope(actor_id=ACTOR, project_ids=frozenset({project.id}), role='owner')
            identity = args.plan_id
            if args.evidence_id:
                identity = views.reproduction(scope, args.evidence_id)
            control = VerificationControl(store)
            control.get(scope, identity)
            result = control.command(scope, identity, VerificationCommand(
                operation='rerun' if args.evidence_id else args.operation,
                expected_revision=args.expected_revision, idempotency_key=args.key))
            with attach_coordinator(database) as client:
                client.status()
        print(command_receipt(result).model_dump_json(indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        return _error(error)
    finally:
        if store is not None:
            store.close()


def verify_command(args) -> int:
    if args.plan_id or args.evidence_id:
        return _registered_command(args)
    if (args.reproduce or args.operation != 'start' or args.project is not None or args.database is not None
            or args.expected_revision is not None or args.key is not None):
        return _error(StoreError('command_mode'))
    stopped = Event()
    previous = None
    if current_thread() is main_thread():
        previous = signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        verdict = VerificationEngine(Path(args.output).expanduser()).verify(
            Path(args.repo).expanduser(), timeout=args.timeout, trust=args.trust,
            reruns=args.reruns, should_stop=stopped.is_set)
        print(json.dumps(verdict.model_dump(mode='json'), ensure_ascii=False, indent=2))
        return 130 if verdict.status == 'cancelled' else 0 if verdict.status == 'passed' else 1
    except (OSError, ValueError) as exc:
        print(f'Verification setup failed: {exc}', file=sys.stderr)
        return 2
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)
