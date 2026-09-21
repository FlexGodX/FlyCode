"""Unit tests for working and episodic memory (plan §32-34)."""

from flycode.memory import Episode, EpisodicMemory, WorkingMemory


def test_working_memory_evicts_fifo():
    memory = WorkingMemory(capacity=3)
    for i in range(5):
        memory.add(f"fact-{i}")
    assert memory.as_list() == ["fact-2", "fact-3", "fact-4"]


def test_episodic_memory_persists_and_reloads(tmp_path):
    path = tmp_path / "episodes.jsonl"
    memory = EpisodicMemory(path)
    memory.add(Episode(task="fix the db connection", lessons=["restart postgres before querying"]))

    reloaded = EpisodicMemory(path)
    assert len(reloaded.all()) == 1
    assert reloaded.all()[0].task == "fix the db connection"


def test_recall_lessons_uses_keyword_overlap():
    memory = EpisodicMemory()
    memory.add(Episode(task="fix the postgres connection", lessons=["start postgres first"]))
    memory.add(Episode(task="refactor the html templates", lessons=["irrelevant lesson"]))

    lessons = memory.recall_lessons("postgres connection is refused")
    assert "start postgres first" in lessons
    assert "irrelevant lesson" not in lessons
