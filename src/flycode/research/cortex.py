"""Explicit cortex/model budgets shared by isolated experiments."""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..llm import HeuristicCandidateGenerator, make_lmstudio_cortex
from ..llm.lmstudio import validate_model_id
from .tournament import TournamentCortex

FixtureName = Literal['toy_arithmetic_bug', 'toy_import_bug', 'search_helper_bug', 'boundary_regression_bug']


def fixture_split(name: str) -> str:
    return 'teacher_toy' if name in {'toy_arithmetic_bug', 'toy_import_bug'} else 'heldout_synthetic'


class CortexConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    cortex: Literal['heuristic', 'tournament', 'lmstudio'] = 'heuristic'
    model: str = Field(default='qwen/qwen3.5-9b', min_length=1)
    decision_model: str | None = Field(default='jev-style-qwen3.5-2b-decision', min_length=1)
    base_url: str | None = None
    request_timeout: float = Field(default=120., gt=0, le=600)
    max_model_calls: int = Field(default=60, ge=1, le=1000)

    _model_ids = field_validator('model', 'decision_model')(validate_model_id)

    @model_validator(mode='after')
    def distinct_local_models(self):
        if self.cortex == 'lmstudio' and self.model == self.decision_model:
            raise ValueError('Reasoner and decider require distinct model IDs')
        if self.cortex == 'lmstudio':
            self.base_url = (self.base_url or os.environ.get('LMSTUDIO_BASE_URL') or 'http://127.0.0.1:1234/v1').rstrip('/')
        return self

    def create_cortex(self):
        if self.cortex == 'lmstudio':
            from openai import APIError

            try:
                return make_lmstudio_cortex(model=self.model, decision_model=self.decision_model,
                    base_url=self.base_url, timeout=self.request_timeout, max_model_calls=self.max_model_calls)
            except APIError as exc:
                raise RuntimeError(f'Local model setup failed: {type(exc).__name__}') from exc
        return TournamentCortex() if self.cortex == 'tournament' else HeuristicCandidateGenerator()
