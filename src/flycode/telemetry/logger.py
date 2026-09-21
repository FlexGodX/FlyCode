"""JSONL telemetry logger for training steps and run metrics (plan §66)."""

from pathlib import Path

from .schema import RunMetrics, TrainingStep


class TelemetryLogger:
    """Appends `TrainingStep`/`RunMetrics` records to JSONL files."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def log_step(self, run_id: str, step: TrainingStep) -> None:
        path = self.directory / f"{run_id}.steps.jsonl"
        with path.open("a") as f:
            f.write(step.model_dump_json() + "\n")

    def log_run(self, metrics: RunMetrics) -> None:
        path = self.directory / "runs.jsonl"
        with path.open("a") as f:
            f.write(metrics.model_dump_json() + "\n")

    def read_steps(self, run_id: str) -> list[TrainingStep]:
        path = self.directory / f"{run_id}.steps.jsonl"
        if not path.exists():
            return []
        return [TrainingStep.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]

    def read_runs(self) -> list[RunMetrics]:
        path = self.directory / "runs.jsonl"
        if not path.exists():
            return []
        return [RunMetrics.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
