"""A selected Git subfolder must not disclose sibling inventory paths."""

import os
import subprocess

import pytest

from flycode.verification.snapshot import capture


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True,
        env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repository'
    root.mkdir()
    git(root, '-c', 'init.defaultBranch=main', 'init')
    project = root / 'selected project'
    project.mkdir()
    (project / 'original module.py').write_text('VALUE = 1\n')
    (root / 'private sibling.py').write_text('VALUE = 2\n')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-m', 'Baseline')
    return root, project


def test_subfolder_inventory_excludes_siblings_and_uses_project_relative_names(repository, tmp_path):
    root, project = repository
    (root / 'private sibling.py').write_text('VALUE = 3\n')
    (root / 'untracked sibling.py').write_text('VALUE = 4\n')
    (project / 'original module.py').write_text('VALUE = 5\n')
    (project / 'new module.py').write_text('VALUE = 6\n')
    before = git(root, 'status', '--porcelain=v1', '-z').stdout
    snapshot = capture(project, tmp_path / 'snapshot')
    assert snapshot.inventory == [' M original module.py', '?? new module.py']
    assert set(snapshot.files) == {'original module.py', 'new module.py'}
    assert (tmp_path / 'snapshot' / 'original module.py').read_text() == 'VALUE = 5\n'
    assert git(root, 'status', '--porcelain=v1', '-z').stdout == before
    assert (root / 'private sibling.py').read_text() == 'VALUE = 3\n'


def test_subfolder_rename_preserves_nul_delimited_origin_and_destination(repository):
    root, project = repository
    git(root, 'mv', 'selected project/original module.py', 'selected project/renamed\nмодуль.py')
    snapshot = capture(project)
    assert snapshot.inventory == ['R  renamed\nмодуль.py', 'original module.py']
    assert set(snapshot.files) == {'renamed\nмодуль.py'}


@pytest.mark.parametrize('direction', ['in', 'out'])
def test_rename_crossing_project_boundary_discloses_only_selected_side(repository, direction):
    root, project = repository
    if direction == 'in':
        git(root, 'mv', 'private sibling.py', 'selected project/imported.py')
        expected = ['A  imported.py']
    else:
        git(root, 'mv', 'selected project/original module.py', 'exported sibling.py')
        expected = ['D  original module.py']
    assert capture(project).inventory == expected


def test_repository_root_rename_keeps_both_relative_names(repository):
    root, _ = repository
    git(root, 'mv', 'private sibling.py', 'renamed sibling.py')
    assert capture(root).inventory == ['R  renamed sibling.py', 'private sibling.py']
