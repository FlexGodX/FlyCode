"""The offline cortex derives repairs from repository evidence, not fixture IDs."""

from flycode.domain import Observation
from flycode.llm import CortexContext, HeuristicCandidateGenerator


def candidates(files, **state):
    obs = Observation(task="run service", file_tree=list(files), **state)
    ctx = CortexContext(task=obs.task, behavior_mode="recover", observation=obs,
                        working_memory=[f"read_file({p}):\n{c}" for p, c in files.items()])
    return HeuristicCandidateGenerator().generate_candidates(ctx)


def test_port_repair_uses_documented_url():
    actions = candidates({
        "compose.yaml": 'services:\n  web:\n    build: .\n    ports: ["9001:80"]\n',
        "README.md": "Check http://localhost:9000/health",
    })
    fix = actions[0]
    assert fix.tool == "edit_file"
    assert fix.arguments == {"path": "compose.yaml", "old_text": "9001:80", "new_text": "9000:80"}


def test_migration_location_uses_existing_script_directory():
    actions = candidates({
        "compose.yaml": 'services:\n  web:\n    build: .\n',
        "alembic.ini": "[alembic]\nscript_location = wrong\n",
        "schema/env.py": "from alembic import context\n",
    })
    assert actions[0].tool == "edit_file"
    assert actions[0].arguments["new_text"] == "script_location = schema"


def test_env_reference_repair_uses_provided_key_without_changing_value():
    actions = candidates({
        "compose.yaml": 'services:\n  web:\n    build: .\n    environment:\n      POSTGRES_PASSWORD: test-value\n',
        "web/database.py": 'value = os.environ.get("POSTGRES_PASS", "default")\n',
    })
    assert actions[0].tool == "edit_file"
    assert actions[0].arguments["old_text"] == 'os.environ.get("POSTGRES_PASS", "default")'
    assert actions[0].arguments["new_text"] == 'os.environ.get("POSTGRES_PASSWORD", "default")'


def test_route_repair_uses_client_contract():
    actions = candidates({
        "compose.yaml": 'services:\n  web:\n    build: .\n',
        "web/main.py": '@app.get("/item")\ndef items():\n    return []\n',
        "tests/test_api.py": 'assert client.get("/items").status_code == 200\n',
    })
    assert actions[0].tool == "edit_file"
    assert actions[0].arguments["new_text"] == '@app.get("/items")'


def test_running_stack_is_checked_with_explicit_database_service():
    actions = candidates({
        "compose.yaml": 'services:\n  web:\n    build: .\n  storage:\n    image: postgres:15\n',
    }, docker={"required": True, "running": True}, database={"required": True})
    assert actions[0].tool == "postgres_status"
    assert actions[0].arguments["service"] == "storage"


def test_only_verified_environment_can_stop():
    actions = candidates({
        "compose.yaml": 'services:\n  web:\n    build: .\n',
    }, docker={"required": True, "running": True}, tests={"status": "passed"},
       services={"required_urls": ["http://localhost:9000/health"]})
    assert actions[0].tool == "service_health"
    assert actions[0].arguments["url"] == "http://localhost:9000/health"
