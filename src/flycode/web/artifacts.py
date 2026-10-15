"""Server-owned controller and admission choices; clients never provide paths."""

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel

from ..research.benchmark import BrainSpec
from ..research.identity import implementation_fingerprint
from ..research.validation import ValidationArtifact


class ControllerChoice(BaseModel):
    id: str
    label: str
    spec: BrainSpec


class ArtifactCatalogue:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.controllers: dict[str, ControllerChoice] = {}
        self.admissions: dict[str, Path] = {}

    def confined(self, path: Path) -> Path:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise ValueError('Artifact leaves the configured catalogue')
        if resolved.is_dir():
            for child in resolved.rglob('*'):
                if child.is_symlink() and not child.resolve().is_relative_to(self.root):
                    raise ValueError('Artifact payload leaves the configured catalogue')
        return resolved

    def _add(self, spec: BrainSpec, label: str, path: Path | None = None, data: bytes = b'') -> None:
        key = hashlib.sha256(str(path).encode() + data).hexdigest()[:20] if path else spec.brain
        self.controllers[key] = ControllerChoice(id=key, label=label, spec=spec)

    def refresh(self) -> dict:
        self.controllers.clear()
        self.admissions.clear()
        self._add(BrainSpec(brain='functional'), 'Functional Fly · функциональная модель')
        self._add(BrainSpec(brain='full'), 'FlyWire · граф по умолчанию')
        manifests = []
        for filename in ('manifest.json', 'checkpoint.json', 'distillation.json'):
            for index, path in enumerate(self.root.rglob(filename)):
                if index >= 2000:
                    break
                try:
                    path = self.confined(path)
                    self.confined(path.parent)
                    if path.stat().st_size > 2 * 1024 ** 2:
                        continue
                    raw = path.read_bytes()
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        continue
                    manifests.append((path, raw, data))
                except (OSError, ValueError):
                    continue
        graphs = {}
        for path, raw, data in manifests:
            if path.name != 'manifest.json' or not data.get('fingerprint') or not data.get('neurons'):
                continue
            kind = 'reduced' if data.get('parent_fingerprint') else 'full'
            graphs[data['fingerprint']] = (path.parent, kind)
            label = f"{'Reduced' if kind == 'reduced' else 'FlyWire'} · {data['neurons']:,} нейронов"
            self._add(BrainSpec(brain=kind, connectome_path=path.parent), label, path, raw)
        for path, raw, data in manifests:
            if path.name == 'checkpoint.json' and data.get('graph_fingerprint') in graphs:
                graph, kind = graphs[data['graph_fingerprint']]
                seed = data.get('metadata', {}).get('seed', '?')
                self._add(BrainSpec(brain=kind, connectome_path=graph, checkpoint=path.parent),
                          f'Обученный {kind} · seed {seed}', path, raw)
            elif path.name == 'distillation.json' and data.get('accepted') is True:
                self._add(BrainSpec(brain='functional', checkpoint=path.parent),
                          f"Functional Fly · student v{data.get('format_version', '?')}", path, raw)
        admissions = []
        # Only conventional validation filenames are indexed; reports/trajectories remain on disk.
        for pattern in ('*validation*.json', '*admission*.json'):
            for index, path in enumerate(self.root.rglob(pattern)):
                if index >= 2000:
                    break
                try:
                    path = self.confined(path)
                    if path.stat().st_size > 2 * 1024 ** 2:
                        continue
                    raw = path.read_bytes()
                    artifact = ValidationArtifact.model_validate_json(raw)
                    report = self.confined(path.parent / artifact.report_path)
                    self.confined(report.parent / 'trajectories')
                    key = hashlib.sha256(str(path).encode() + raw).hexdigest()[:20]
                    self.admissions[key] = path
                    current = artifact.implementation_sha256 == implementation_fingerprint()
                    mode_label = 'Ограниченное влияние' if artifact.mode == 'bounded' else 'Автономный режим'
                    admissions.append({'id': key, 'mode': artifact.mode, 'scope': artifact.scope,
                        'max_influence': artifact.max_influence, 'available': artifact.accepted and current,
                        'label': f'{mode_label} · {artifact.controller_id}',
                        'reason': '' if artifact.accepted and current else 'Требуется повторная проверка текущей версии'})
                except (OSError, ValueError):
                    continue
        labels = [item.label for item in self.controllers.values()]
        return {'controllers': [{'id': item.id, 'label': item.label + (f' · {item.id[:6]}'
                                 if labels.count(item.label) > 1 else ''), 'brain': item.spec.brain,
                                 'learning': item.spec.brain == 'functional'} for item in self.controllers.values()],
                'admissions': list({item['id']: item for item in admissions}.values())}

    def resolve(self, key: str) -> BrainSpec:
        if key not in self.controllers:
            raise ValueError('Контроллер недоступен. Обновите страницу и выберите локальный артефакт.')
        spec = self.controllers[key].spec
        for path in (spec.connectome_path, spec.checkpoint):
            if path is not None:
                self.confined(path)
        return spec

    def admission(self, key: str | None) -> Path:
        if key not in self.admissions:
            raise ValueError('Для этого режима нужен действующий допуск. Выберите его в настройках.')
        return self.confined(self.admissions[key])


def default_artifact_root() -> Path:
    return Path(os.environ.get('FLYCODE_ARTIFACT_ROOT', str(Path.home() / '.cache/flycode')))
