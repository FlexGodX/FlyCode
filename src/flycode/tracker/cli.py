"""Trusted local operator access to the same canonical queue used by web."""

import argparse
import json
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

from ..domain.orchestration import (
    AccessScope,
    TaskCommand,
    TaskDraft,
    TaskState,
    WorkflowBudget,
    WorkflowDraft,
)
from ..domain.project import TaskStopReceipt
from ..orchestration.attach import CoordinatorClient, attach_coordinator
from ..orchestration.projects import Projects
from ..orchestration.store import Store, StoreError, default_state_path
from .commands import CommentCommand
from .service import TrackerService

ACTOR = 'cli:local-owner-v1'


def _identity(raw):
    if not re.fullmatch(r'[0-9a-f]{32}', raw):
        raise argparse.ArgumentTypeError('expected a canonical 32-character hexadecimal ID')
    return raw


def _cursor(raw):
    value = int(raw)
    if not 0 <= value <= 2**63 - 1:
        raise argparse.ArgumentTypeError('cursor must be in 0..2**63-1')
    return value


def _limit(raw):
    value = int(raw)
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError('limit must be in 1..100')
    return value


def configure_parser(subparsers):
    parser = subparsers.add_parser('tasks', help='Manage the shared canonical task queue')
    parser.add_argument('--database', help='State database; defaults to FLYCODE_STATE_DATABASE or local state')
    commands = parser.add_subparsers(dest='task_action', required=True)
    service = commands.add_parser('service', help='Check or explicitly connect the shared coordinator')
    service.add_argument('service_action', choices=['status', 'attach'])
    for name in ('register', 'create', 'list', 'show', 'command', 'comment', 'events', 'agents', 'stops', 'stop'):
        command = commands.add_parser(name)
        if name not in {'stops', 'stop'}:
            command.add_argument('--project', required=True, help='Explicit current local project folder')
        if name in {'show', 'command', 'comment', 'stop'}:
            command.add_argument('task_id', type=_identity)
        if name in {'create', 'comment', 'stop'}:
            command.add_argument('--key', required=True, help='Stable idempotency key; reuse for the same request')
        if name in {'create', 'command'}:
            command.add_argument('--input', required=True,
                help='TaskDraft JSON file' if name == 'create' else 'Complete TaskCommand JSON file')
        if name == 'create':
            command.add_argument('--workflow', type=_identity, help='Existing same-project workflow ID')
            command.add_argument('--workflow-budget', help='WorkflowBudget JSON file for a new root only')
        if name in {'list', 'events', 'stops'}:
            command.add_argument('--after', type=_cursor, default=0)
            command.add_argument('--limit', type=_limit, default=50)
        if name == 'list':
            command.add_argument('--state', choices=get_args(TaskState))
            command.add_argument('--query', default='')
            command.add_argument('--archived', action='store_true')
        if name == 'events':
            command.add_argument('--task', dest='task_id', type=_identity)
        if name == 'comment':
            command.add_argument('--text', required=True)
        if name == 'stop':
            command.add_argument('--operation', choices=['pause', 'cancel'], required=True)
            command.add_argument('--expected-revision', type=int, required=True)


def _json(path):
    descriptor = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise StoreError('invalid_input')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(1024 * 1024 + 1)
    finally:
        os.close(descriptor)
    if len(raw) > 1024 * 1024:
        raise StoreError('invalid_input')
    try:
        value = json.loads(raw)
        json.dumps(value, allow_nan=False)
    except RecursionError as error:
        raise ValueError('Input nesting exceeds the JSON parser limit') from error
    return value


def tasks_command(args) -> int:
    store = None
    try:
        action = args.task_action
        payload: TaskDraft | TaskCommand | CommentCommand | None = None
        result: BaseModel | dict[str, Any] | list[dict[str, Any]]
        budget = None
        if action == 'create':
            if args.workflow and args.workflow_budget:
                raise StoreError('scope')
            payload = TaskDraft.model_validate(_json(args.input))
            budget = WorkflowBudget.model_validate(_json(args.workflow_budget)) if args.workflow_budget else None
        elif action == 'command':
            payload = TaskCommand.model_validate(_json(args.input))
        elif action == 'comment':
            payload = CommentCommand(text=args.text, idempotency_key=args.key)
        elif action == 'stop':
            payload = TaskCommand(operation=args.operation, expected_revision=args.expected_revision, idempotency_key=args.key)
        if action in {'create', 'comment', 'stop'} and not 1 <= len(args.key) <= 200:
            raise StoreError('idempotency')
        if action == 'list' and len(args.query) > 200:
            raise StoreError('cursor')
        database = Path(args.database).expanduser().resolve() if args.database else default_state_path()
        if action == 'service':
            with (attach_coordinator(database) if args.service_action == 'attach' else CoordinatorClient(database)) as client:
                print(client.status().model_dump_json(indent=2))
            return 0
        store = Store(database)
        projects, tracker = Projects(store), TrackerService(store)
        if action == 'stops':
            result = projects.stop_page(ACTOR, after=args.after, limit=args.limit)
        elif action == 'stop':
            assert isinstance(payload, TaskCommand)
            task = projects.stop_command(ACTOR, args.task_id, payload)
            result = TaskStopReceipt(task_id=task.id, revision=task.revision, state=task.state, paused=task.paused)
        else:
            project = (projects.register if action == 'register' else projects.resolve)(Path(args.project).expanduser(), actor_id=ACTOR)
            scope = AccessScope(actor_id=ACTOR, project_ids=frozenset({project.id}), role='owner')
            if action == 'register':
                result = {'project_id': project.id}
            elif action == 'create':
                assert isinstance(payload, TaskDraft)
                if args.workflow:
                    store.workflow(scope, args.workflow)
                    identity = store.create_task(scope, args.workflow, payload, key=args.key).id
                else:
                    workflow = tracker.create(scope, WorkflowDraft(project_id=project.id, task=payload, budget=budget), key=args.key)
                    identity = workflow.root_task_id
                result = tracker.task(scope, identity)
            elif action == 'list':
                result = tracker.page(scope, after=args.after, limit=args.limit, state=args.state, q=args.query, archived=args.archived)
            elif action == 'show':
                result = tracker.task(scope, args.task_id)
            elif action == 'command':
                assert isinstance(payload, TaskCommand)
                tracker.command(scope, args.task_id, payload.model_dump())
                result = tracker.task(scope, args.task_id)
            elif action == 'comment':
                assert isinstance(payload, CommentCommand)
                result = tracker.comment(scope, args.task_id, payload)
            elif action == 'events':
                if args.task_id:
                    result = tracker.task_events(scope, args.task_id, after=args.after, limit=args.limit)
                else:
                    events, cursor = tracker.events(scope, after=args.after, limit=args.limit)
                    result = {'items': [event.model_dump(mode='json') for event in events], 'cursor': cursor}
            else:
                result = [entry.model_dump(mode='json') for entry in tracker.agents(scope)]
        if isinstance(result, BaseModel):
            result = result.model_dump(mode='json')
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except StoreError as error:
        detail = {'code': error.code}
        if error.code == 'revision' and error.current is not None:
            detail['revision'] = error.current['revision']
        print(json.dumps({'error': detail}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({'error': {'code': 'local_unavailable'}}), file=sys.stderr)
        return 2
    except ValueError:
        print(json.dumps({'error': {'code': 'invalid_fields'}}), file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()
