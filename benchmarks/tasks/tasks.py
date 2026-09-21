"""Benchmark tasks for FlyCode evaluation."""

from dataclasses import dataclass
from typing import List
from pathlib import Path


@dataclass
class BenchmarkTask:
    """Single benchmark task."""
    id: str
    name: str
    category: str
    description: str
    expected_outcome: str
    repo_path: Path | None = None
    expected_steps: int = 10
    
    # Task characteristics
    difficulty: str = "easy"  # easy, medium, hard
    expected_category: str = None  # "simple_bug", "search", "docker", "database", etc.
    
    # Evaluation metrics
    min_success_rate: float = 0.7
    max_steps: int = 20
    max_tool_calls: int = 15
    
    # Constraints
    timeout: float = 300.0  # seconds
    max_llm_calls: int = 10
    matched_compute: bool = True  # Compare with matched compute baselines


# Define task categories
CATEGORY_SIMPLE = "simple_bug"  # Obvious failing unit test
CATEGORY_SEARCH = "search"  # Need to find existing helper
CATEGORY_REGRESSION = "regression"  # Edit causes new failure
CATEGORY_DOCKER = "docker"  # Docker config broken
CATEGORY_DATABASE = "database"  # DB not running/migration broken
CATEGORY_HTTP = "http"  # Endpoint returns 500
CATEGORY_PAROBS = "partial_observability"  # Some observations hidden
CATEGORY_DELAYED = "delayed_reward"  # Consequences after 10-20 steps
CATEGORY_REVERSAL = "context_reversal"  # Previous strategy becomes wrong
CATEGORY_NOISY = "noisy_feedback"  # Stochastic test results
CATEGORY_NOVEL = "novel_repository"  # Unknown framework
CATEGORY_RESOURCE = "resource_constrained"  # Limited tool calls

# Task definitions
BENCHMARK_TASKS = [
    # Simple bugs
    BenchmarkTask(
        id="task_01",
        name="fix_syntax_error",
        category=CATEGORY_SIMPLE,
        description="Fix syntax error in Python file",
        expected_outcome="Syntax errors fixed, tests pass",
        expected_steps=5,
        difficulty="easy",
    ),
    BenchmarkTask(
        id="task_02",
        name="fix_missing_import",
        category=CATEGORY_SIMPLE,
        description="Fix missing import statement",
        expected_outcome="Import added, tests pass",
        expected_steps=5,
        difficulty="easy",
    ),
    
    # Search tasks
    BenchmarkTask(
        id="task_03",
        name="find_logging_helper",
        category=CATEGORY_SEARCH,
        description="Add logging to function. Use existing logging helpers in the codebase.",
        expected_outcome="Logging added to target function",
        expected_steps=8,
        difficulty="medium",
        expected_category="search",
    ),
    
    # Regression tasks
    BenchmarkTask(
        id="task_04",
        name="regression_broken_endpoint",
        category=CATEGORY_REGRESSION,
        description="Make /users endpoint return 200. Previous edit broke it.",
        expected_outcome="Endpoint returns 200",
        expected_steps=12,
        difficulty="medium",
        expected_category="regression",
    ),
    
    # Docker tasks
    BenchmarkTask(
        id="task_05",
        name="fix_docker_ports",
        category=CATEGORY_DOCKER,
        description="Fix docker-compose.yml. Port 5432 is occupied.",
        expected_outcome="Docker starts successfully",
        expected_steps=8,
        difficulty="medium",
        expected_category="docker",
    ),
    
    # Database tasks
    BenchmarkTask(
        id="task_06",
        name="fix_db_connection",
        category=CATEGORY_DATABASE,
        description="Fix PostgreSQL connection error",
        expected_outcome="Database connects successfully",
        expected_steps=10,
        difficulty="medium",
        expected_category="database",
    ),
    
    # HTTP tasks
    BenchmarkTask(
        id="task_07",
        name="fix_http_500",
        category=CATEGORY_HTTP,
        description="Fix 500 error on /api/health endpoint",
        expected_outcome="Returns 200 OK",
        expected_steps=6,
        difficulty="easy",
        expected_category="http",
    ),
    
    # Complex tasks
    BenchmarkTask(
        id="task_08",
        name="fastapi_full_app",
        category="end_to_end",
        description="Fix FastAPI app with PostgreSQL. Get docker running and all tests passing.",
        expected_outcome="Working FastAPI app with DB",
        expected_steps=15,
        difficulty="hard",
    ),
    BenchmarkTask(
        id="task_09",
        name="react_component_bug",
        category="complex_bug",
        description="Fix null reference in React component",
        expected_outcome="Component renders without errors",
        expected_steps=8,
        difficulty="medium",
    ),
]

# Benchmark configuration
BENCHMARK_CONFIG = {
    "default_timeout": 300.0,
    "default_seed_range": (42, 127),
    "required_bases": ["llm_only", "fsm", "rnn", "functional"],
    "optional_bases": ["reduced", "full", "constrained"],
    
    "matched_compute": {
        "functional": {"params": 1000, "state_size": 50, "latency_ms": 100},
        "rnn": {"params": 1000, "state_size": 50, "latency_ms": 80},
        "fsm": {"params": 100, "state_size": 10, "latency_ms": 5},
    },
    
    "required_tasks": [
        "task_01", "task_02", "task_03", "task_05", "task_06", "task_08"
    ],
    
    "runs_per_seed": 5,
    "seeds": [42, 127, 256, 512, 777],
}

# Success criteria
BENCHMARK_SUCCESS_CRITERIA = {
    "research": {
        "min_tasks": 10,
        "min_seeds": 5,
        "min_effect_size": 0.1,  # 10% improvement
        "stability": 0.8,  # 80% of runs show same result
    },
    "engineering": {
        "quality_improvement": 0.05,  # 5% better quality
        "tool_reduction": -10,  # 10 fewer tool calls
        "latency_acceptable": True,  # Within 2x of baseline
    },
}