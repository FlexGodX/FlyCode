"""Episodic memory - one record per completed task (plan §33)."""

from pathlib import Path

from pydantic import BaseModel, Field


class Episode(BaseModel):
    task: str
    initial_state: dict = Field(default_factory=dict)
    key_observations: list[str] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    failures: list[dict] = Field(default_factory=list)
    successful_patterns: list[dict] = Field(default_factory=list)
    final_result: str = ""
    lessons: list[str] = Field(default_factory=list)


class EpisodicMemory:
    """Append-only JSONL store of episodes, with simple lesson recall."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._episodes: list[Episode] = []
        if path and path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    self._episodes.append(Episode.model_validate_json(line))

    def add(self, episode: Episode) -> None:
        self._episodes.append(episode)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(episode.model_dump_json() + "\n")

    def all(self) -> list[Episode]:
        return list(self._episodes)

    def recall_lessons(self, task: str, limit: int = 5) -> list[str]:
        """Naive keyword overlap recall - good enough until embeddings land."""
        task_words = set(task.lower().split())
        scored = []
        for episode in self._episodes:
            overlap = len(task_words & set(episode.task.lower().split()))
            if overlap > 0:
                scored.append((overlap, episode))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        lessons: list[str] = []
        for _, episode in scored:
            lessons.extend(episode.lessons)
            if len(lessons) >= limit:
                break
        return lessons[:limit]
