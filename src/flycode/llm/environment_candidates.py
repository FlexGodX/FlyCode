"""Narrow offline environment repairs inferred from repository files (§78).

This is a deterministic demo cortex, not a general configuration repairer.
It never consults fixture identities or defect metadata.
"""

import re
from difflib import get_close_matches
from pathlib import PurePosixPath

import yaml

COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def environment_candidates(obs, files, propose, repair):
    compose_path = next((p for p in COMPOSE_FILES if p in obs.file_tree), None)
    if compose_path is None:
        return []

    def one(tool, arguments, rationale):
        return [propose(tool, arguments, rationale, expected_progress=0.8)]

    def edit(path, old, new):
        return one("edit_file", {"path": path, "old_text": old, "new_text": new},
                   "reconcile configuration with the repository's declared contract")

    if obs.is_healthy:
        return one("stop", {}, "tests, migration and service checks passed")

    needed = [compose_path]
    if obs.docker.get("running") is not True:
        needed += [p for p in obs.file_tree if p in ("README.md", "alembic.ini") or (
            p.endswith(".py") and not p.endswith("__init__.py") and "/versions/" not in p
        )]
    for path in needed:
        if path not in files:
            return one("read_file", {"path": path}, "inspect service configuration and its client contract")

    try:
        config = yaml.safe_load(files[compose_path])
    except yaml.YAMLError:
        return []
    if not isinstance(config, dict) or not isinstance(config.get("services"), dict):
        return []
    services = config["services"]
    app = next((name for name, service in services.items() if isinstance(service, dict) and "build" in service), None)
    db = next((name for name, service in services.items()
               if isinstance(service, dict) and str(service.get("image", "")).startswith("postgres:")), None)
    if app is None:
        return []

    if obs.docker.get("running") is not True:
        documented_ports = set(re.findall(r"https?://(?:localhost|127\.0\.0\.1):(\d+)", files.get("README.md", "")))
        if len(documented_ports) == 1:
            port = next(iter(documented_ports))
            for mapping in services[app].get("ports", []):
                match = re.fullmatch(r"(\d+):(\d+)", str(mapping))
                if match and match[1] != port:
                    return edit(compose_path, str(mapping), f"{port}:{match[2]}")

        ini = files.get("alembic.ini", "")
        location = re.search(r"^script_location\s*=\s*(\S+)", ini, re.MULTILINE)
        migration_dirs = [str(PurePosixPath(p).parent) for p in obs.file_tree
                          if p.endswith("/env.py") and "alembic" in files.get(p, "")]
        if location and len(migration_dirs) == 1 and location[1] != migration_dirs[0]:
            return edit("alembic.ini", location[0], f"script_location = {migration_dirs[0]}")

        env = services[app].get("environment", {})
        keys = list(env) if isinstance(env, dict) else [entry.split("=", 1)[0] for entry in env]
        for path, content in files.items():
            if not path.endswith(".py"):
                continue
            for match in re.finditer(r'''os\.environ\.get\((['"])(?P<key>\w+)\1[^)\n]*\)''', content):
                key = match["key"]
                replacements = get_close_matches(key, keys, n=1, cutoff=0.8)
                if key not in keys and replacements:
                    return edit(path, match[0], match[0].replace(key, replacements[0], 1))

        expected_routes = set()
        for path, content in files.items():
            if path.startswith("tests/"):
                expected_routes.update(re.findall(r'''client\.get\(['"]([^'"]+)['"]''', content))
        routes = {}
        for path, content in files.items():
            if path.startswith("tests/"):
                continue
            for match in re.finditer(r'''@\w+\.get\((['"])(?P<route>/[^'"]*)\1\)''', content):
                routes[match["route"]] = (path, match[0])
        missing = expected_routes - routes.keys()
        for route in sorted(missing):
            similar = get_close_matches(route, sorted(routes.keys() - expected_routes), n=1, cutoff=0.8)
            if similar:
                old = similar[0]
                path, decorator = routes[old]
                return edit(path, decorator, decorator.replace(old, route))

        if obs.docker.get("last_action") == "up":
            return []  # an unchanged failed start needs semantic diagnosis
        return one("docker_compose", {"action": "up"}, "build and wait for the declared stack")

    if db and obs.database.get("connected") is not True:
        return one("postgres_status", {"service": db}, "check PostgreSQL inside its service")
    if obs.database.get("migration_required") and obs.database.get("migration_applied") is not True:
        return one("alembic", {"service": app, "action": "upgrade", "revision": "head"}, "apply the migration chain")
    if obs.tests_status != "passed":
        fixes = repair()
        return fixes or one("run_tests", {"service": app}, "verify the application inside its runtime")
    for url in obs.services.get("required_urls", []):
        if obs.services.get("checked_urls", {}).get(url) is not True:
            tool = "service_health" if url.rstrip("/").endswith("/health") else "http_request"
            return one(tool, {"url": url}, "verify the published service endpoint")
    return []
