"""Analyze recorded recommendations, preserving missing/error denominators (§59)."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..telemetry import TrainingStep


class ShadowComparison(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    influence: float = Field(ge=0, le=1)
    active_choice: str = Field(min_length=1)
    shadow_choice: str | None = Field(default=None, min_length=1)
    agreement: bool | None = None
    risk_delta: float | None = Field(default=None, ge=-1, le=1)
    observer_latency_ms: float = Field(ge=0)
    error: str | None = None
    counterfactual_success: None = None
    tool_call_delta: None = None

    @model_validator(mode='after')
    def consistent_recommendation(self):
        expected = self.shadow_choice == self.active_choice if self.shadow_choice is not None else None
        if self.agreement != expected:
            raise ValueError('Agreement does not match the recorded choices')
        return self


def analyze_shadow(directory: Path, run_ids: set[str] | None = None) -> dict:
    traces = []
    legacy = invalid = 0
    files = sorted(directory.glob('*.steps.jsonl'))
    if run_ids is not None:
        files = [path for path in files if path.name.removesuffix('.steps.jsonl') in run_ids]
    if not files:
        raise ValueError('No recorded trajectory files found')
    for path in files:
        with path.open() as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    step = TrainingStep.model_validate_json(line)
                    if step.shadow_decision is None:
                        legacy += 1
                        continue
                    traces.append(ShadowComparison.model_validate(step.shadow_decision))
                except ValueError:
                    invalid += 1
    available = [trace for trace in traces if trace.shadow_choice is not None and not trace.error]
    risks = [trace.risk_delta for trace in available if trace.risk_delta is not None]
    return {'files': len(files), 'attempted_decisions': len(traces),
            'available_recommendations': len(available), 'legacy_steps': legacy,
            'invalid_records': invalid, 'observer_errors': sum(bool(trace.error) for trace in traces),
            'coverage': len(available) / len(traces) if traces else 0.,
            'agreement_rate': sum(trace.agreement is True for trace in available) / len(available) if available else None,
            'mean_expected_risk_delta': sum(risks) / len(risks) if risks else None,
            'mean_observer_latency_ms': sum(trace.observer_latency_ms for trace in traces) / len(traces) if traces else None,
            'zero_influence': all(trace.influence == 0. for trace in traces) if traces else None,
            'counterfactual_success': None, 'tool_call_delta': None,
            'scope': 'Recorded recommendations only; no alternative actions were executed by this analysis.'}


def save_shadow_analysis(directory: Path, output: Path) -> dict:
    report = analyze_shadow(directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False))
    return report
