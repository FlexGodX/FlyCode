"""Trusted pytest observer; invoked explicitly by the registered runner profile."""

import json
import os


def _write(event):
    with open(os.environ['FLYCODE_CASE_REPORT'], 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(event) + '\n')


def pytest_collection_finish(session):
    _write({'kind': 'collection', 'ids': [item.nodeid for item in session.items]})


def pytest_collectreport(report):
    if report.failed or report.skipped:
        _write({'kind': 'collection_result', 'id': report.nodeid,
                'outcome': 'error' if report.failed else 'skipped'})


def pytest_runtest_logreport(report):
    if report.when == 'call' or report.failed:
        outcome = report.outcome
        if report.when != 'call':
            outcome = 'error'
        elif hasattr(report, 'wasxfail'):
            outcome = 'xpass' if report.passed else 'xfail'
        elif report.failed and '[XPASS(strict)]' in str(report.longrepr):
            outcome = 'xpass'
        _write({'kind': 'case', 'id': report.nodeid, 'outcome': outcome, 'duration': report.duration, 'phase': report.when})
    elif report.when == 'setup' and report.skipped:
        _write({'kind': 'case', 'id': report.nodeid, 'outcome': 'xfail' if hasattr(report, 'wasxfail') else 'skipped',
                'duration': report.duration, 'phase': report.when})


def pytest_sessionfinish(session, exitstatus):
    _write({'kind': 'finished', 'exit_code': int(exitstatus)})
