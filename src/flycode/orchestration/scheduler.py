"""Deterministic baseline and mandatory oldest-first dispatch (ORC-05)."""

from collections.abc import Callable

from ..domain.orchestration import WorkItem


def baseline(task: WorkItem, now: float, cost: float) -> float:
    age = min(1., max(0., now - (task.queued_at if task.queued_at is not None else task.created_at)) / 600.)
    urgency = 0. if task.due_at is None else min(1., max(0., 1. - (task.due_at - now) / 86400.))
    return (.30 * task.priority / 3. + .20 * task.critical_path_fraction + .15 * urgency + .15 * age
            + .10 * task.expected_progress - .05 * min(1., max(0., cost)) - .05 * task.risk)


def ordered(tasks: list[WorkItem], now: float, successful_starts: int, *, root: bool = False,
            cost: float | Callable[[WorkItem], float] = 0.) -> list[WorkItem]:
    def age(task):
        return task.queued_at if task.queued_at is not None else task.created_at

    if (successful_starts + 1) % 8 == 0:
        return sorted(tasks, key=lambda task: (age(task), task.id))
    if root:
        return sorted(tasks, key=lambda task: (-task.priority, age(task), task.id))
    return sorted(tasks, key=lambda task: (-baseline(task, now, cost(task) if callable(cost) else cost), age(task), task.id))
