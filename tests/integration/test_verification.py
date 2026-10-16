"""Verification observes real snapshot bytes and actual pytest outcomes."""

import json
import os
import signal
import subprocess
import time
from threading import Event, Thread

import pytest


def project(tmp_path, code):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'test_example.py').write_text(code)
    return root


@pytest.mark.parametrize(('code', 'status', 'outcome'), [
    ('def test_ok(): assert 2 + 2 == 4', 'passed', 'passed'),
    ('def test_bad(): assert 2 + 2 == 5', 'failed', 'failed'),
    ('import pytest\n@pytest.mark.skip\ndef test_skipped(): assert False', 'inconclusive', 'skipped'),
    ('import pytest\n@pytest.mark.xfail\ndef test_known(): assert False', 'inconclusive', 'xfail'),
    ('import pytest\n@pytest.mark.xfail\ndef test_unexpected(): assert True', 'inconclusive', 'xpass'),
    ('def test_error(missing_fixture): pass', 'inconclusive', 'error'),
])
def test_real_pytest_outcomes_do_not_confuse_execution_with_success(tmp_path, code, status, outcome):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, code)
    engine = VerificationEngine(tmp_path / 'evidence')
    verdict = engine.verify(root)
    assert verdict.status == status, verdict
    assert verdict.cases[0].outcome == outcome
    assert verdict.subject_hash and verdict.evidence_id
    manifest = json.loads((tmp_path / 'evidence' / verdict.evidence_id / 'manifest.json').read_text())
    assert manifest['subject_hash'] == verdict.subject_hash
    assert manifest['invocations'][0]['argv'][1:3] == ['-m', 'pytest']
    assert manifest['trust'] == 'trusted-local-process'
    assert (root / 'test_example.py').read_text() == code


def test_empty_collection_and_unsupported_hostile_sandbox_never_pass(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, '# no tests')
    engine = VerificationEngine(tmp_path / 'evidence')
    assert engine.verify(root).status == 'inconclusive'
    blocked = engine.verify(root, trust='untrusted')
    assert blocked.status == 'inconclusive'
    assert 'sandbox' in ' '.join(blocked.reasons).lower()
    assert not blocked.cases


def test_dirty_and_untracked_bytes_invalidate_old_result(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, 'def test_ok(): assert True')
    engine = VerificationEngine(tmp_path / 'evidence')
    verdict = engine.verify(root)
    assert engine.current(verdict, root)
    (root / 'new.py').write_text('VALUE = 1')
    assert not engine.current(verdict, root)
    (root / 'new.py').unlink()
    assert engine.current(verdict, root)
    (root / 'test_example.py').write_text('def test_ok(): assert False')
    assert not engine.current(verdict, root)


def test_snapshot_excludes_credentials_and_rejects_symlink_escape(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, 'def test_ok(): assert True')
    (root / '.env').write_text('SECRET=never-copy-this')
    engine = VerificationEngine(tmp_path / 'evidence')
    verdict = engine.verify(root)
    bundle = tmp_path / 'evidence' / verdict.evidence_id
    assert not any(b'never-copy-this' in path.read_bytes() for path in bundle.rglob('*') if path.is_file())
    assert '.env' in json.loads((bundle / 'snapshot.json').read_text())['excluded']
    (root / 'escape').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='symlink'):
        engine.verify(root)


def test_timeout_and_cancel_stop_owned_process_and_preserve_partial_evidence(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, 'import time\ndef test_slow(): time.sleep(30)')
    engine = VerificationEngine(tmp_path / 'evidence')
    timed = engine.verify(root, timeout=.3)
    assert timed.status == 'inconclusive'
    assert any('timeout' in reason.lower() for reason in timed.reasons)
    stop = Event()
    result = []
    worker = Thread(target=lambda: result.append(engine.verify(root, should_stop=stop.is_set)))
    worker.start()
    stop.set()
    worker.join(8)
    assert not worker.is_alive()
    assert result[0].status == 'cancelled'


def test_tests_cannot_modify_the_original_project(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, "from pathlib import Path\ndef test_mutation(): Path('product.py').write_text('after')")
    (root / 'product.py').write_text('before')
    result = VerificationEngine(tmp_path / 'evidence').verify(root)
    assert (root / 'product.py').read_text() == 'before'
    assert result.status == 'invalidated'


def test_broken_report_cannot_supply_false_green(tmp_path):
    from flycode.verification.runner import parse_junit

    report = tmp_path / 'report.xml'
    report.write_text('<!DOCTYPE data [<!ENTITY secret SYSTEM "file:///etc/passwd">]><testsuites>&secret;</testsuites>')
    with pytest.raises(ValueError):
        parse_junit(report)
    report.write_text('<testsuites><testsuite tests="2"><testcase name="one"/></testsuite></testsuites>')
    with pytest.raises(ValueError):
        parse_junit(report)


def test_snapshot_preserves_executable_mode_and_mode_change_invalidates(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, "import os\ndef test_mode(): assert os.access('run.sh', os.X_OK)")
    script = root / 'run.sh'
    script.write_text('#!/bin/sh\nexit 0\n')
    script.chmod(0o755)
    engine = VerificationEngine(tmp_path / 'evidence')
    verdict = engine.verify(root)
    assert verdict.status == 'passed'
    script.chmod(0o644)
    assert not engine.current(verdict, root)


def test_forged_verdict_status_cannot_reuse_genuine_evidence(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, 'def test_bad(): assert False')
    engine = VerificationEngine(tmp_path / 'evidence')
    verdict = engine.verify(root)
    assert verdict.status == 'failed'
    assert not engine.current(verdict.model_copy(update={'status': 'passed'}), root)


def test_passing_pytest_does_not_leave_background_children_running(tmp_path):
    from flycode.verification import VerificationEngine

    marker, pid_file = tmp_path / 'late-write', tmp_path / 'child.pid'
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text('leaked')"
    code = ("import subprocess, sys\nfrom pathlib import Path\ndef test_child():\n"
            f"    p = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            f"    Path({str(pid_file)!r}).write_text(str(p.pid))\n")
    root = project(tmp_path, code)
    try:
        engine = VerificationEngine(tmp_path / 'evidence')
        result = engine.verify(root)
        assert result.status == 'inconclusive'
        assert result.cases[0].outcome == 'passed'
        manifest = json.loads((engine.data_dir / result.evidence_id / 'manifest.json').read_text())
        attempt = manifest['invocations'][0]
        assert not attempt['complete'] and attempt['stop_confirmed']
        assert attempt['process_reason'] == 'descendants_terminated'
        time.sleep(1.2)
        assert not marker.exists(), 'Owned subprocess escaped runner cleanup'
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_persistent_failure_wins_over_another_flaky_case(tmp_path):
    from flycode.verification import VerificationEngine

    counter = tmp_path / 'attempt-counter'
    code = ("from pathlib import Path\ndef test_flaky():\n"
            f"    counter = Path({str(counter)!r})\n"
            "    existed = counter.exists()\n    counter.touch()\n    assert existed\n"
            "def test_always_bad(): assert False\n")
    root = project(tmp_path, code)
    result = VerificationEngine(tmp_path / 'evidence').verify(root, reruns=1)
    assert result.status == 'failed'
    assert len(result.cases) == 4
    assert len([case for case in result.cases if case.flaky]) == 2


def test_cli_verification_returns_structured_verdict_and_nonzero_for_empty_suite(tmp_path, capsys):
    from flycode.cli import main

    root = project(tmp_path, 'def test_ok(): assert True')
    arguments = ['verify', '--repo', str(root), '--output', str(tmp_path / 'evidence')]
    assert main(arguments) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict['status'] == 'passed' and verdict['required_coverage'] == 1
    (root / 'test_example.py').write_text('# empty')
    assert main(arguments) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'inconclusive'


def test_runner_chmod_invalidates_evidence_for_original_modes(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, "from pathlib import Path\ndef test_chmod(): Path('product.py').chmod(0o755)")
    (root / 'product.py').write_text('VALUE = 1')
    (root / 'product.py').chmod(0o644)
    assert VerificationEngine(tmp_path / 'evidence').verify(root).status == 'invalidated'


def test_rerun_cannot_hide_collection_changes(tmp_path):
    from flycode.verification import VerificationEngine

    flag = tmp_path / 'collection-flag'
    code = (f"from pathlib import Path\nflag = Path({str(flag)!r})\n"
            "if not flag.exists():\n    def test_first(): flag.touch()\n"
            "else:\n    def test_second(): assert True\n")
    root = project(tmp_path, code)
    result = VerificationEngine(tmp_path / 'evidence').verify(root, reruns=1)
    assert result.status == 'inconclusive'
    assert 'collection' in ' '.join(result.reasons).lower()


def test_verifier_implementation_change_invalidates_approval(tmp_path, monkeypatch):
    from flycode.research import identity
    from flycode.verification import VerificationEngine

    implementation = tmp_path / 'runtime'
    implementation.mkdir()
    (implementation / 'agent.py').write_text('agent = 1')
    (implementation / 'cli.py').write_text('cli = 1')
    (implementation / 'verification').mkdir()
    policy = implementation / 'verification' / 'verdict.py'
    policy.write_text('allow_empty = False')
    monkeypatch.setattr(identity, '__file__', str(implementation / 'research' / 'identity.py'))
    root = project(tmp_path, 'def test_ok(): assert True')
    engine = VerificationEngine(tmp_path / 'evidence')
    verdict = engine.verify(root)
    assert engine.current(verdict, root)
    policy.write_text('allow_empty = True')
    assert not engine.current(verdict, root)


def test_non_utf8_xml_cannot_bypass_dtd_rejection(tmp_path):
    from flycode.verification.runner import parse_junit

    report = tmp_path / 'encoded.xml'
    report.write_bytes(('<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE testsuites [<!ENTITY x "expanded">]>'
        '<testsuites><testsuite tests="1"><testcase name="&x;"/></testsuite></testsuites>').encode('utf-16'))
    with pytest.raises(ValueError):
        parse_junit(report)


def test_oversized_stdout_never_becomes_complete_passing_evidence(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, 'def test_output(): print("x" * (3 * 1024 * 1024))')
    (root / 'pytest.ini').write_text('[pytest]\naddopts = -s\n')
    result = VerificationEngine(tmp_path / 'evidence').verify(root)
    assert result.status == 'inconclusive'
    assert 'size limit' in ' '.join(result.reasons)


def test_teardown_error_preserves_confirmed_assertion_failure(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, 'import pytest\n@pytest.fixture\ndef cleanup():\n'
                   '    yield\n    raise RuntimeError("cleanup failed")\n'
                   'def test_bad(cleanup): assert False\n')
    result = VerificationEngine(tmp_path / 'evidence').verify(root)
    assert result.status == 'failed'
    assert any(case.outcome == 'failed' and 'teardown:error' in case.diagnostics for case in result.cases)


def test_collection_errors_have_explicit_case_evidence(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, 'def invalid syntax !!!')
    result = VerificationEngine(tmp_path / 'evidence').verify(root)
    assert result.status == 'inconclusive'
    assert any(case.outcome == 'error' for case in result.cases)
    assert 'collection' in ' '.join(result.reasons).lower()


def test_stop_between_planned_attempts_never_returns_passed(tmp_path, monkeypatch):
    from flycode.verification import VerificationEngine, engine

    stopped = Event()
    actual_runner = engine.run_pytest

    def stop_after_actual_run(*args):
        result = actual_runner(*args)
        stopped.set()
        return result

    monkeypatch.setattr(engine, 'run_pytest', stop_after_actual_run)
    root = project(tmp_path, 'def test_ok(): assert True')
    result = VerificationEngine(tmp_path / 'evidence').verify(root, reruns=1, should_stop=stopped.is_set)
    assert result.status == 'cancelled'
    assert result.cases[0].outcome == 'passed'
    manifest = json.loads((tmp_path / 'evidence' / result.evidence_id / 'manifest.json').read_text())
    assert len(manifest['invocations']) == 1 and not manifest['complete']


def test_git_worktree_metadata_cannot_mutate_original_index(tmp_path):
    from flycode.verification import VerificationEngine

    base, worktree = tmp_path / 'base', tmp_path / 'worktree'
    base.mkdir()
    subprocess.run(['git', 'init', str(base)], check=True, capture_output=True)
    (base / 'README').write_text('base')
    subprocess.run(['git', '-C', str(base), 'add', 'README'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(base), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-m', 'Fixture base'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(base), 'worktree', 'add', '--detach', str(worktree)], check=True, capture_output=True)
    (worktree / 'test_git.py').write_text("from pathlib import Path\nimport subprocess\ndef test_git():\n"
        "    Path('only-in-copy.py').write_text('created')\n"
        "    subprocess.run(['git', 'add', 'only-in-copy.py'], check=False)\n")
    index = subprocess.run(['git', '-C', str(worktree), 'rev-parse', '--git-path', 'index'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from pathlib import Path

    index_bytes = Path(index).read_bytes()
    before = subprocess.run(['git', '-C', str(worktree), 'status', '--porcelain'],
                            check=True, capture_output=True).stdout
    VerificationEngine(tmp_path / 'evidence').verify(worktree)
    after = subprocess.run(['git', '-C', str(worktree), 'status', '--porcelain'],
                           check=True, capture_output=True).stdout
    assert before == after
    assert Path(index).read_bytes() == index_bytes


def test_junit_case_rename_cannot_match_collected_cases_by_count_only(tmp_path):
    from flycode.verification import VerificationEngine

    root = project(tmp_path, 'def test_original(): assert True')
    (root / 'conftest.py').write_text('import pytest\nimport xml.etree.ElementTree as ET\n'
        '@pytest.hookimpl(trylast=True)\ndef pytest_sessionfinish(session, exitstatus):\n'
        '    tree = ET.parse(session.config.option.xmlpath)\n'
        '    tree.find(".//testcase").set("name", "not_collected")\n'
        '    tree.write(session.config.option.xmlpath)\n')
    result = VerificationEngine(tmp_path / 'evidence').verify(root)
    assert result.status == 'inconclusive'
    assert 'mismatch' in ' '.join(result.reasons).lower()


def test_redaction_preserves_well_formed_structured_evidence(tmp_path):
    from flycode.verification import VerificationEngine
    from flycode.verification.runner import parse_junit

    root = project(tmp_path, 'import pytest\ndef test_bad(): pytest.fail("password=demo-secret")')
    result = VerificationEngine(tmp_path / 'evidence').verify(root)
    report = tmp_path / 'evidence' / result.evidence_id / 'attempt-1' / 'junit.xml'
    assert result.status == 'failed'
    assert parse_junit(report)[0].outcome == 'failed'
    assert b'demo-secret' not in report.read_bytes()


def test_snapshot_budget_counts_actual_bytes_when_file_size_metadata_is_stale(tmp_path, monkeypatch):
    from pathlib import Path

    from flycode.verification import snapshot

    root = tmp_path / 'source'
    root.mkdir()
    (root / 'a.bin').write_bytes(b'a' * 80)
    (root / 'b.bin').write_bytes(b'b' * 80)
    actual_stat = Path.stat

    def stale_stat(path, *args, **kwargs):
        result = actual_stat(path, *args, **kwargs)
        if path.parent == root and path.name in {'a.bin', 'b.bin'}:
            values = list(result)
            values[6] = 20
            return os.stat_result(values)
        return result

    monkeypatch.setattr(snapshot, 'MAX_BYTES', 128)
    monkeypatch.setattr(Path, 'stat', stale_stat)
    with pytest.raises(ValueError, match='limit'):
        snapshot.input_files(root)


def test_pem_header_constant_is_code_while_private_key_material_is_rejected(tmp_path):
    from flycode.verification.snapshot import input_files

    root = tmp_path / 'source'
    root.mkdir()
    path = root / 'parser.py'
    code = b"PEM_HEADER = b'-----BEGIN PRIVATE KEY-----'"
    path.write_bytes(code)
    assert input_files(root)[0]['parser.py'] == code
    path.write_bytes(b'-----BEGIN RSA PRIVATE KEY-----\n' + b'A' * 64 + b'\n-----END RSA PRIVATE KEY-----')
    with pytest.raises(ValueError, match='Credential'):
        input_files(root)
