"""Held-out fixtures contain a real defect and preserve nonbroken behavior."""

import os
import subprocess
import sys

import pytest

from flycode.environment.fixtures import materialize


@pytest.mark.parametrize(('fixture', 'file', 'replacement'), [
    ('search_helper_bug', 'billing/service.py',
     'from . import money\n\ndef invoice_total(items):\n    return money.cents_to_amount(sum(items))\n'),
    ('boundary_regression_bug', 'shipping.py',
     ('def shipping_cost(total):\n    if total < 0:\n        raise ValueError("negative total")\n'
      '    return 0 if total >= 50 else 5\n')),
])
def test_heldout_task_fails_then_correct_repair_passes_and_reset_is_identical(tmp_path, fixture, file, replacement):
    repo = tmp_path / fixture
    materialize(fixture, repo)
    original = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob('*') if p.is_file()}

    def check():
        return subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'],
            cwd=repo, env={**os.environ, 'PYTHONPATH': str(repo), 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=30, check=False)

    broken = check()
    assert broken.returncode == 1, broken.stdout + broken.stderr
    if fixture == 'search_helper_bug':
        (repo / file).write_text('def invoice_total(items):\n    return sum(items) / 100\n')
        shortcut = check()
        assert shortcut.returncode == 1, 'Numeric examples alone must not pass the shared-helper requirement'
    (repo / file).write_text(replacement)
    repaired = check()
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    materialize(fixture, repo)
    assert {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob('*') if p.is_file()} == original
