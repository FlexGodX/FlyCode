"""Endpoint-scoped discovery and explicit, bounded compatibility probes (§102)."""

import hashlib
import os
from threading import Lock
from typing import Literal

import httpx
from openai import APIError
from pydantic import BaseModel, Field

from ..domain import CandidateAction, Observation
from .decision import JevStyleDecisionClient
from .lmstudio import DEFAULT_BASE_URL
from .openai_compatible import OpenAICompatibleLLM
from .protocol import CortexContext

ModelRole = Literal['primary', 'scorer']


class ModelDescriptor(BaseModel):
    revision: int = 1
    model_ref: str
    model_id: str
    display_name: str
    provider: str = 'lmstudio'
    endpoint_ref: str
    status: Literal['discovered', 'ready', 'unavailable', 'unsupported'] = 'discovered'
    roles: list[ModelRole] = Field(default_factory=list)
    capability_version: str = 'structured-candidates+option-logprobs-v1'
    capability_source: str = 'not-probed'
    weights_revision: str | None = None
    probe_calls: int = 0
    reason: str = 'Совместимость не проверена. Обнаружение не означает загрузку в память.'


class ModelCatalogue:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get('LMSTUDIO_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')
        self.endpoint_ref = hashlib.sha256(self.base_url.encode()).hexdigest()[:20]
        self.entries: dict[str, ModelDescriptor] = {}
        self.lock = Lock()

    def discover(self) -> list[ModelDescriptor]:
        key = os.environ.get('LMSTUDIO_API_KEY') or 'lm-studio'
        with httpx.Client(timeout=3, trust_env=False) as client:
            response = client.get(self.base_url + '/models', headers={'Authorization': f'Bearer {key}'})
            response.raise_for_status()
            data = response.json()['data']
        ids = {entry['id'] for entry in data if isinstance(entry.get('id'), str) and 0 < len(entry['id']) <= 200}
        with self.lock:
            for entry in self.entries.values():
                if entry.model_id not in ids and entry.status != 'unavailable':
                    entry.revision += 1
                    entry.status = 'unavailable'
                    entry.roles = []
                    entry.reason = 'Model unavailable at the configured endpoint'
            for model_id in sorted(ids):
                ref = hashlib.sha256(f'{self.endpoint_ref}:{model_id}'.encode()).hexdigest()[:24]
                if ref not in self.entries or self.entries[ref].status == 'unavailable':
                    revision = self.entries[ref].revision + 1 if ref in self.entries else 1
                    self.entries[ref] = ModelDescriptor(model_ref=ref, model_id=model_id, display_name=model_id,
                                                        endpoint_ref=self.endpoint_ref, revision=revision)
            return [entry.model_copy(deep=True) for entry in self.entries.values()]

    def resolve(self, model_ref: str, role: ModelRole, capability_version: str) -> ModelDescriptor | None:
        """Read an admitted protocol snapshot without HTTP or waiting for a probe."""
        if not self.lock.acquire(blocking=False):
            return None
        try:
            entry = self.entries.get(model_ref)
            if (entry is None or entry.status != 'ready' or role not in entry.roles
                    or entry.capability_version != capability_version or entry.capability_source != 'explicit-local-probe'):
                return None
            return entry.model_copy(deep=True)
        finally:
            self.lock.release()

    def probe(self, model_ref: str, role: ModelRole) -> ModelDescriptor:
        if role not in {'primary', 'scorer'}:
            raise ValueError('Unknown model role')
        # One probe per endpoint; a second caller cannot start concurrent GPU requests.
        if not self.lock.acquire(blocking=False):
            raise ValueError('Model probe is already running')
        try:
            entry = self.entries.get(model_ref)
            if entry is None or entry.status == 'unavailable':
                raise ValueError('Model unavailable; refresh the catalogue')
            key = os.environ.get('LMSTUDIO_API_KEY') or 'lm-studio'
            context = CortexContext(task='Inspect sample.py without editing it', behavior_mode='explore',
                                    observation=Observation(task='compatibility probe'))
            entry.probe_calls += 1
            entry.revision += 1
            try:
                if role == 'primary':
                    client = OpenAICompatibleLLM(model=entry.model_id, base_url=self.base_url, api_key=key,
                        structured_output=True, max_tokens=1536, timeout=30)
                    if not client.generate_candidates(context):
                        raise ValueError('No valid structured candidates')
                else:
                    scorer = JevStyleDecisionClient(entry.model_id, self.base_url, key, timeout=30)
                    scorer.score(context, [CandidateAction(id='a', tool='read_file', arguments={'path': 'sample.py'}),
                                           CandidateAction(id='b', tool='list_files')])
                if role not in entry.roles:
                    entry.roles.append(role)
                entry.status = 'ready'
                entry.capability_source = 'explicit-local-probe'
                entry.reason = 'Протокол проверен одним запросом; качество решений и версия весов не установлены.'
            except (APIError, ValueError) as exc:
                entry.roles = [known for known in entry.roles if known != role]
                entry.status = 'ready' if entry.roles else 'unsupported'
                entry.reason = f'Compatibility probe failed: {type(exc).__name__}'
            return entry.model_copy(deep=True)
        finally:
            self.lock.release()
