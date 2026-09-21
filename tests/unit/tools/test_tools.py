"""Unit tests for the MVP tool registry (plan §49)."""

from flycode.tools import run_tool


def test_create_read_edit_roundtrip(tmp_path):
    run_tool("create_file", tmp_path, path="a.txt", content="hello")
    result = run_tool("read_file", tmp_path, path="a.txt")
    assert result.success
    assert result.payload["content"] == "hello"

    result = run_tool("edit_file", tmp_path, path="a.txt", old_text="hello", new_text="world")
    assert result.success
    result = run_tool("read_file", tmp_path, path="a.txt")
    assert result.payload["content"] == "world"


def test_list_files_excludes_vcs_dirs(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / "a.py").write_text("x = 1\n")

    result = run_tool("list_files", tmp_path)
    assert "a.py" in result.payload["files"]
    assert not any(".git" in f for f in result.payload["files"])


def test_search_finds_pattern(tmp_path):
    (tmp_path / "a.py").write_text("def add(a, b):\n    return a + b\n")
    result = run_tool("search", tmp_path, pattern="def add")
    assert result.success
    assert len(result.payload["matches"]) == 1
    assert result.payload["matches"][0]["file"] == "a.py"


def test_path_escape_is_rejected(tmp_path):
    result = run_tool("read_file", tmp_path, path="../outside.txt")
    assert not result.success
    assert "escapes" in result.error_message


def test_unknown_tool_reports_error(tmp_path):
    result = run_tool("not_a_tool", tmp_path)
    assert not result.success
    assert "unknown tool" in result.error_message


def test_run_tests_reports_pass_fail(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert 1 == 1\n")
    result = run_tool("run_tests", tmp_path)
    assert result.success
    assert result.payload["passed"] == 1
    assert result.payload["status"] == "passed"


def test_git_status_reports_untracked(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x")
    result = run_tool("git_status", tmp_path)
    assert result.success
    assert "a.txt" in result.payload["untracked"]
    assert result.payload["dirty"]
