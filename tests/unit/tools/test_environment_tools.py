"""Unit tests for the environment tools and their registration (plan §50)."""

from flycode.domain import VALID_TOOLS
from flycode.tools import TOOL_REGISTRY, ToolResult, register_environment_tools, run_tool
from flycode.tools.environment_tools import ALEMBIC_SUBCOMMANDS

ENVIRONMENT_TOOLS = (
    "docker_compose",
    "docker_logs",
    "postgres_status",
    "postgres_query",
    "alembic",
    "http_request",
    "service_health",
)


def test_environment_tools_are_registered():
    register_environment_tools()
    for name in ENVIRONMENT_TOOLS:
        assert name in TOOL_REGISTRY, f"{name} is declared in VALID_TOOLS but not dispatchable"


def test_every_proposable_tool_is_dispatchable():
    """`stop` is handled by the environment itself; everything else must resolve."""
    register_environment_tools()
    assert set(TOOL_REGISTRY) == set(VALID_TOOLS) - {"stop"}


def test_alembic_subcommands_are_whitelisted(tmp_path):
    result = run_tool("alembic", tmp_path, action="rm -rf")

    assert not result.success
    assert "unsupported alembic subcommand" in result.error_message
    assert result.payload["subcommands"] == sorted(ALEMBIC_SUBCOMMANDS)


def test_alembic_reports_a_broken_migration_chain_as_data(tmp_path):
    """No alembic.ini (or no alembic binary) is routine data, never an exception."""
    result = run_tool("alembic", tmp_path, action="upgrade", revision="head")

    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.error_message


def test_docker_tool_degrades_instead_of_raising(tmp_path):
    result = run_tool("docker_compose", tmp_path, action="ps")

    assert isinstance(result, ToolResult)
    if not result.success:
        assert result.error_message
