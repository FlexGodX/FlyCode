"""SoftwareGym - the software-engineering environment (plan §6).

Includes deterministic fixtures, SoftwareGym, and infrastructure setup.
"""

from ..domain import Observation, ObservationDelta, TaskSpec, ToolAction, Transition

from .fixtures import FixtureConfig, FixtureCategory, FIXTURES, MaterializeResult, materialize
from .observer import observe
from .software_gym import SoftwareEnvironment

__all__ = [
    "SoftwareEnvironment",
    "TaskSpec",
    "Observation",
    "ObservationDelta",
    "Transition",
    "ToolAction",
    "observe",
    "materialize",
    "FIXTURES",
    "FixtureConfig",
    "FixtureCategory",
    "MaterializeResult",
]
