"""Risk reflex - detects high-risk actions and blocks or redirects them (plan §29-30).

Safety always wins over fly control. This module is the last gate before an
action reaches the environment; nothing upstream (fly bias, LLM ranking) may
bypass it.
"""

import re

from pydantic import BaseModel, Field

from ..domain import ToolAction

# Patterns that identify irreversible / destructive operations (plan §30).
# Matched against the tool name and the string form of its arguments.
HIGH_RISK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"drop\s+(database|table|schema)", "database drop"),
    (r"truncate\s+table", "table truncate"),
    (r"delete\s+from\s+\w+\s*(;|$)", "unbounded delete (no WHERE)"),
    (r"rm\s+-rf", "recursive force delete"),
    (r"git\s+reset\s+--hard", "git history discard"),
    (r"git\s+push\s+--force", "force push"),
    (r"git\s+clean\s+-f", "untracked file wipe"),
    (r"--no-verify", "hook bypass"),
    (r"docker\s+.*volume\s+rm", "volume deletion"),
    (r"docker\s+.*(system\s+prune|-v\b)", "volume/data prune"),
    (r"credential|api[_-]?key|secret|password", "credential change"),
    (r"history\s+rewrite|rebase\s+-i|filter-branch", "history rewrite"),
)

# Tool/argument combinations that are always high risk regardless of pattern match.
HIGH_RISK_TOOLS: frozenset[str] = frozenset({"alembic"})  # downgrade / stamp can be destructive


class RiskAssessment(BaseModel):
    """Outcome of a risk evaluation for one candidate action."""

    risk_level: float = Field(default=0.0, ge=0.0, le=1.0)
    is_blocked: bool = False
    reasons: list[str] = Field(default_factory=list)

    @property
    def is_high_risk(self) -> bool:
        return self.risk_level >= 0.7


def _action_text(action: ToolAction) -> str:
    parts = [action.tool]
    for value in action.arguments.values():
        parts.append(str(value))
    return " ".join(parts).lower()


def assess_risk(action: ToolAction) -> RiskAssessment:
    """Score an action's risk and flag whether it must be blocked (plan §30)."""
    text = _action_text(action)
    reasons: list[str] = []

    for pattern, label in HIGH_RISK_PATTERNS:
        if re.search(pattern, text):
            reasons.append(label)

    risk = action.expected_risk
    if reasons:
        risk = max(risk, 0.9)
    if action.tool in HIGH_RISK_TOOLS and any(
        kw in text for kw in ("downgrade", "stamp", "--sql")
    ):
        risk = max(risk, 0.8)
        reasons.append(f"{action.tool} destructive subcommand")

    risk = max(0.0, min(1.0, risk))
    return RiskAssessment(risk_level=risk, is_blocked=risk >= 0.7, reasons=reasons)


def requires_verification(action: ToolAction, assessment: RiskAssessment) -> bool:
    """Risky-but-not-blocked actions should route through VERIFY_RISK (plan §30)."""
    return 0.4 <= assessment.risk_level < 0.7 and not assessment.is_blocked
