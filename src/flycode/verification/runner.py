"""Registered pytest argv, owned process cancellation and bounded structured output."""

import json
import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..domain.verification import AttemptError, CaseOutcome, CaseResult, CheckAttempt

OUTPUT_LIMIT = 2 * 1024 * 1024
REPORT_LIMIT = 8 * 1024 * 1024


def parse_junit(path: Path) -> list[CaseResult]:
    if not path.is_file() or path.stat().st_size > REPORT_LIMIT:
        raise ValueError('Missing or oversized JUnit report')
    data = path.read_bytes()
    if b'\0' in data or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('XML declarations are forbidden')
    try:
        root = ET.fromstring(data)
        if root.tag not in {'testsuites', 'testsuite'}:
            raise ValueError('Unexpected JUnit root')
        cases = []
        for suite in root.iter('testsuite'):
            nodes = list(suite.iter('testcase'))
            if int(suite.get('tests', '-1')) != len(nodes):
                raise ValueError('JUnit collection count mismatch')
        for node in root.iter('testcase'):
            outcome: CaseOutcome = 'passed'
            if node.find('error') is not None:
                outcome = 'error'
            elif node.find('failure') is not None:
                failure = node.find('failure')
                assert failure is not None
                outcome = 'xpass' if '[XPASS(strict)]' in failure.get('message', '') else 'failed'
            elif node.find('skipped') is not None:
                skipped = node.find('skipped')
                assert skipped is not None
                outcome = 'xfail' if skipped.get('type') == 'pytest.xfail' else 'skipped'
            cases.append(CaseResult(id=f"{node.get('classname', '')}::{node.get('name', '')}",
                                    outcome=outcome, duration=float(node.get('time', '0'))))
        if len(cases) > 20000:
            raise ValueError('Too many JUnit cases')
        return cases
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise ValueError('Invalid or inconsistent JUnit report') from exc


def _group_gone(group_id: int, *, process: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process is not None:
            try:
                process.wait(timeout=0)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # Permission failure alone proves neither existence nor exit.
        time.sleep(.05)
    return False


def _signal_group(process: subprocess.Popen, sig: int) -> bool:
    try:
        os.killpg(process.pid, sig)
        return True
    except ProcessLookupError:
        process.wait(timeout=0)
        return False
    except PermissionError:
        # The leader may become waitable after the permission error. Reap only
        # our owned process while independently checking the complete group.
        if _group_gone(process.pid, process=process):
            return False
        raise


def _terminate(process: subprocess.Popen) -> None:
    if not _signal_group(process, signal.SIGTERM):
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        process.wait(timeout=5)
    # The leader can exit while children still hold the owned process group.
    if not _group_gone(process.pid, process=process):
        _signal_group(process, signal.SIGKILL)
        if not _group_gone(process.pid, process=process):
            raise OSError('Owned process group termination is unconfirmed')


def _junit_id(node_id: str) -> str:
    address, separator, parameters = node_id.partition('[')
    parts = address.split('::')
    module = parts[0].removesuffix('.py').replace('/', '.')
    name = parts[-1] + (separator + parameters if separator else '')
    return '.'.join([module, *parts[1:-1]]) + '::' + name


def run_pytest(sandbox: Path, output: Path, timeout: float, number: int,
               should_stop: Callable[[], bool], *, targets: Sequence[str] = (),
               on_state: Callable[[str], None] = lambda state: None) -> CheckAttempt:
    from .plan import validate_target

    for target in targets:
        validate_target(target)
    output.mkdir()
    report = output / 'junit.xml'
    observer = output / 'cases.jsonl'
    argv = [sys.executable, '-m', 'pytest', '-q', '-p', 'pytest_observer',
            '-o', 'xfail_strict=true', f'--junitxml={report}', f'--basetemp={output / "tmp"}']
    argv.extend(['--', *targets])
    environment = {name: os.environ[name] for name in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if name in os.environ}
    environment.update({'HOME': str(sandbox / '.home'), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
        'PYTHONPATH': os.pathsep.join([str(Path(__file__).parent), str(sandbox), str(sandbox / 'src')]),
        'FLYCODE_CASE_REPORT': str(observer), 'GIT_CEILING_DIRECTORIES': str(sandbox.parent)})
    reasons, cases, collected = [], [], []
    started = time.monotonic()
    started_at = datetime.now(timezone.utc)
    on_state('preparing')
    cancelled, complete, exit_code = False, False, None
    process_started = False
    with (output / 'stdout.txt').open('wb') as stdout, (output / 'stderr.txt').open('wb') as stderr:
        if should_stop():
            return CheckAttempt(number=number, argv=argv, exit_code=None, duration=0, cancelled=True,
                                state='cancelled', error_class='cancelled', started_at=started_at,
                                ended_at=datetime.now(timezone.utc),
                                reasons=['cancelled before process start'])
        try:
            process = subprocess.Popen(argv, cwd=sandbox, env=environment, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            process_started = True
            on_state('running')
            try:
                while process.poll() is None:
                    cancelled = should_stop()
                    if cancelled or time.monotonic() - started >= timeout:
                        reasons.append('cancelled' if cancelled else 'runner timeout')
                        _terminate(process)
                        break
                    if any(path.exists() and path.stat().st_size > limit for path, limit in (
                        (output / 'stdout.txt', OUTPUT_LIMIT), (output / 'stderr.txt', OUTPUT_LIMIT),
                        (observer, REPORT_LIMIT), (report, REPORT_LIMIT))):
                        reasons.append('runner output exceeded size limit')
                        _terminate(process)
                        break
                    time.sleep(.05)
                exit_code = process.wait()
            finally:
                _terminate(process)
        except OSError as exc:
            label = 'owned process cleanup unconfirmed' if process_started else 'runner unavailable'
            reasons.append(f'{label}: {type(exc).__name__}')
    if any(path.exists() and path.stat().st_size > limit for path, limit in (
        (output / 'stdout.txt', OUTPUT_LIMIT), (output / 'stderr.txt', OUTPUT_LIMIT),
        (observer, REPORT_LIMIT), (report, REPORT_LIMIT))):
        reasons.append('runner output exceeded size limit')
    on_state('collecting')
    try:
        if not observer.is_file() or observer.stat().st_size > REPORT_LIMIT:
            raise ValueError('Missing or oversized observer report')
        events = [json.loads(line) for line in observer.read_text().splitlines()]
        collections = [event['ids'] for event in events if event['kind'] == 'collection']
        finishes = [event['exit_code'] for event in events if event['kind'] == 'finished']
        if len(collections) != 1:
            raise ValueError('Incomplete observer report')
        collected = collections[0]
        observed: dict[str, CaseResult] = {}
        for event in events:
            if event['kind'] == 'case':
                result = CaseResult(id=event['id'], outcome=event['outcome'], duration=event['duration'], attempt=number)
                previous = observed.get(result.id)
                diagnostic = f"{event['phase']}:{result.outcome}"
                if previous is not None:
                    priority = {'failed': 4, 'error': 3, 'xpass': 2, 'skipped': 1, 'xfail': 1, 'passed': 0}
                    chosen = previous if priority[previous.outcome] >= priority[result.outcome] else result
                    result = chosen.model_copy(update={'duration': previous.duration + result.duration,
                                                       'diagnostics': [*previous.diagnostics, diagnostic]})
                else:
                    result = result.model_copy(update={'diagnostics': [diagnostic]})
                observed[result.id] = result
        cases = list(observed.values())
        collection_results = [CaseResult(id=event['id'], outcome=event['outcome'], attempt=number,
                                         diagnostics=[f"collection:{event['outcome']}"])
                              for event in events if event['kind'] == 'collection_result']
        cases.extend(collection_results)
        if collection_results:
            reasons.extend(f"collection {case.outcome}: {case.id}" for case in collection_results)
        if finishes != [exit_code]:
            reasons.append('Incomplete observer termination report')
        junit = parse_junit(report)
        if (len(collected) != len(set(collected)) or set(collected) != set(observed)
                or (not collection_results and {case.id for case in junit} != {_junit_id(key) for key in observed})):
            reasons.append('Observed/collected case mismatch')
        priority = {'failed': 4, 'error': 3, 'xpass': 2, 'skipped': 1, 'xfail': 1, 'passed': 0}
        reported: dict[str, CaseOutcome] = {}
        for case in junit:
            previous_outcome = reported.get(case.id, 'passed')
            reported[case.id] = max((previous_outcome, case.outcome), key=lambda value: priority[value])
        if any(reported.get(_junit_id(key)) != case.outcome for key, case in observed.items()):
            reasons.append('JUnit/observer outcome mismatch')
        complete = not reasons
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reasons.append(f'incomplete structured report: {type(exc).__name__}')
    error_class: AttemptError | None = ('cleanup' if any('cleanup unconfirmed' in reason for reason in reasons) else
        'cancelled' if cancelled else 'timeout' if 'runner timeout' in reasons else
        'unavailable' if any('unavailable' in reason for reason in reasons) else
        'collection' if any(diagnostic.startswith('collection:') for case in cases for diagnostic in case.diagnostics) else
        'assertion' if any(case.outcome == 'failed' for case in cases) else
        'setup' if any(case.outcome == 'error' for case in cases) else 'report' if reasons else None)
    return CheckAttempt(number=number, argv=argv, exit_code=exit_code, duration=time.monotonic() - started,
                        cases=cases, collected=collected, reasons=reasons, cancelled=cancelled, complete=complete,
                        state='interrupted' if error_class == 'cleanup' else
                              'cancelled' if cancelled else 'blocked' if error_class == 'unavailable' else 'finished',
                        error_class=error_class, started_at=started_at, ended_at=datetime.now(timezone.utc))
