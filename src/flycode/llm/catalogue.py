"""Endpoint-scoped discovery and explicit, bounded compatibility probes (§102)."""

import hashlib
import json
import os
from threading import Lock
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from openai import APIError
from pydantic import BaseModel, Field

from ..domain import CandidateAction, Observation
from ..orchestration.transport import EndpointBusy, HostTransport
from .decision import JevStyleDecisionClient
from .lmstudio import DEFAULT_BASE_URL
from .openai_compatible import OpenAICompatibleLLM
from .protocol import CortexContext

ModelRole = Literal['primary', 'scorer']


class CatalogueBusy(ValueError):
    pass


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
    loaded_instance_id: str | None = None
    context_limit: int | None = Field(default=None, gt=0)
    load_fingerprint: str | None = None
    probe_calls: int = 0
    reason: str = 'Совместимость не проверена. Обнаружение не означает загрузку в память.'


class ModelCatalogue:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get('LMSTUDIO_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')
        self.endpoint_ref = hashlib.sha256(self.base_url.encode()).hexdigest()[:20]
        self.entries: dict[str, ModelDescriptor] = {}
        self.lock = Lock()
        self.probe_owner_id = uuid4().hex

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
                    entry.loaded_instance_id = entry.context_limit = entry.load_fingerprint = None
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

    def _runtime(self, entry: ModelDescriptor) -> None:
        previous = (entry.loaded_instance_id, entry.context_limit, entry.load_fingerprint)
        instance = None
        try:
            address = urlsplit(self.base_url)
            key = os.environ.get('LMSTUDIO_API_KEY') or 'lm-studio'
            with httpx.Client(timeout=3, trust_env=False) as client:
                response = client.get(f'{address.scheme}://{address.netloc}/api/v1/models',
                                      headers={'Authorization': f'Bearer {key}'})
                response.raise_for_status()
                for model in response.json()['models']:
                    if model.get('type') not in {'llm', 'decision'}:
                        continue
                    matches = [item for item in model['loaded_instances'] if item['id'] == entry.model_id]
                    if not matches and model['key'] == entry.model_id and len(model['loaded_instances']) == 1:
                        matches = model['loaded_instances']
                    if matches:
                        candidate = matches[0]
                        capacity = candidate['config']['context_length']
                        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
                            raise ValueError('Invalid loaded context capacity')
                        if not isinstance(candidate['id'], str) or not 0 < len(candidate['id']) <= 200:
                            raise ValueError('Invalid loaded instance identifier')
                        instance = candidate
                        break
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            entry.reason = 'Runtime model metadata unavailable; no inference sent'
        entry.loaded_instance_id = instance['id'] if instance else None
        entry.context_limit = instance['config']['context_length'] if instance else None
        entry.load_fingerprint = hashlib.sha256(json.dumps(instance, sort_keys=True).encode()).hexdigest() if instance else None
        if previous != (entry.loaded_instance_id, entry.context_limit, entry.load_fingerprint):
            entry.revision += 1
        if previous[2] is not None and previous[2] != entry.load_fingerprint:
            entry.roles = []
            entry.capability_source = 'not-probed'
            entry.status = 'discovered'
        if instance is None:
            entry.roles = []
            entry.status = 'unavailable'
            entry.reason = 'No unambiguous loaded instance/capacity; no inference sent'
        elif entry.status == 'unavailable':
            entry.status = 'discovered'

    def refresh_runtime(self, model_ref: str) -> ModelDescriptor:
        """Read loaded configuration explicitly; this never loads or invokes a model."""
        if not self.lock.acquire(blocking=False):
            raise CatalogueBusy('Model catalogue is busy')
        try:
            if model_ref not in self.entries:
                raise ValueError('Unknown model reference')
            entry = self.entries[model_ref]
            self._runtime(entry)
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
            self._runtime(entry)
            if entry.loaded_instance_id is None:
                return entry.model_copy(deep=True)
            key = os.environ.get('LMSTUDIO_API_KEY') or 'lm-studio'
            context = CortexContext(task='Inspect sample.py without editing it', behavior_mode='explore',
                                    observation=Observation(task='compatibility probe'))
            transport = HostTransport(self.base_url, entry.loaded_instance_id, role,
                kind='probe', owner_id=self.probe_owner_id)
            def execute(request):
                def admitted():
                    entry.probe_calls += 1
                    entry.revision += 1
                    return request()
                return transport.execute(admitted, timeout=0)
            try:
                if role == 'primary':
                    client = OpenAICompatibleLLM(model=entry.loaded_instance_id, base_url=self.base_url, api_key=key,
                        structured_output=True, max_tokens=1536, timeout=30, request_executor=execute)
                    if not client.generate_candidates(context.model_copy(update={'n_candidates': 1})):
                        raise ValueError('No valid structured candidates')
                else:
                    scorer = JevStyleDecisionClient(entry.loaded_instance_id, self.base_url, key, timeout=30,
                        request_executor=execute)
                    scorer.score(context, [CandidateAction(id='a', tool='read_file', arguments={'path': 'sample.py'}),
                                           CandidateAction(id='b', tool='list_files')])
                if role not in entry.roles:
                    entry.roles.append(role)
                entry.status = 'ready'
                entry.capability_source = 'explicit-local-probe'
                entry.reason = 'Протокол проверен одним запросом; качество решений и версия весов не установлены.'
            except EndpointBusy as exc:
                raise CatalogueBusy(str(exc)) from exc
            except (APIError, ValueError) as exc:
                entry.roles = [known for known in entry.roles if known != role]
                entry.status = 'ready' if entry.roles else 'unsupported'
                entry.reason = f'Compatibility probe failed: {type(exc).__name__}'
            return entry.model_copy(deep=True)
        finally:
            self.lock.release()
