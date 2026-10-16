"""Bind the existing cortex protocol to one canonical attempt and its budgets."""

import os
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from ..llm.catalogue import CatalogueBusy
from ..llm.lmstudio import LMStudioCortex
from ..llm.openai_compatible import OpenAICompatibleLLM
from ..llm.protocol import CortexContext
from .resources import Resources
from .store import Store, StoreError


class AttemptCortex:
    def __init__(self, store: Store, scope, attempt_id: str, fence: int,
                 should_stop: Callable[[], bool] = lambda: False):
        self.store, self.scope, self.attempt_id, self.fence = store, scope, attempt_id, fence
        self.should_stop = should_stop
        attempt, _, workflow = store._owned(scope, attempt_id, fence, running=True)
        profile = attempt.profile_snapshot
        if not store._binding_current(profile) or profile.loaded_instance_id is None:
            raise StoreError('model_unavailable')
        if profile.role == 'decider':
            raise StoreError('capabilities', 'A scorer is not a structured candidate generator')
        timeout = min(120., workflow.deadline_at - store.clock())
        reasoner = OpenAICompatibleLLM(model=profile.loaded_instance_id, base_url=store.catalogue.base_url,
            api_key=os.environ.get('LMSTUDIO_API_KEY') or 'lm-studio', temperature=0, structured_output=True,
            max_tokens=min(1536, max(1, profile.context_limit // 4)), timeout=timeout,
            context_limit=profile.context_limit, request_executor=self._execute)
        self.core = LMStudioCortex(reasoner, None, max_model_calls=max(1, attempt.initial_budget.model_calls),
                                   should_stop=should_stop)

    def reset(self):
        self.core.reset()

    @property
    def model_calls(self):
        return self.core.model_calls

    @property
    def last_routing(self):
        return self.core.last_routing

    def _execute(self, request: Callable[[], Any]):
        if self.store.db.in_transaction:
            raise RuntimeError('Model transport cannot run in a SQLite transaction')
        while True:
            if self.should_stop():
                raise InterruptedError('Attempt stopped before inference')
            attempt, _, _ = self.store._owned(self.scope, self.attempt_id, self.fence, running=True)
            try:
                self.store.catalogue.refresh_runtime(attempt.profile_snapshot.model_ref)
                break
            except CatalogueBusy:
                time.sleep(.05)
        return Resources(self.store).execute(self.scope, self.attempt_id, self.fence,
            key='inference:' + uuid4().hex, request=request, should_stop=self.should_stop)

    def _context(self, context: CortexContext) -> CortexContext:
        attempt, task, _ = self.store._owned(self.scope, self.attempt_id, self.fence, running=True)
        if task.permissions is None or task.acceptance is None:
            raise StoreError('incomplete')
        text = (f'{attempt.profile_snapshot.instructions}\n\nObjective:\n{task.objective}\n\n'
                f'Acceptance contract:\n{task.acceptance.model_dump_json()}')
        observation = context.observation.model_copy(update={'task': task.objective, 'task_id': task.id})
        return context.model_copy(update={'task': text, 'observation': observation,
            'candidate_tools': list(dict.fromkeys([*task.permissions.tools, 'stop']))})

    def generate_candidates(self, context: CortexContext):
        candidates = self.core.generate_candidates(self._context(context))
        return [item.model_copy(update={'id': uuid4().hex}) for item in candidates]

    def consolidate(self, context: CortexContext, transcript: list[str]) -> str:
        # This summary is local, including after cancellation; it never spends another request.
        return self.core.consolidate(context, transcript)
