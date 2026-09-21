"""Cognition modules that make up the Functional Fly (plan §41).

Includes:
- Task Compass (CX) - persistent low-dimensional task state
- Mushroom Body - fast associative memory with sparse representation
- Valence - dual reward/aversion signals
- Novelty - curiosity and novelty tracking
- RPE - reward prediction error for learning
- Drives - internal motivational states
- Habituation - repeated actions lose attractiveness
- Recovery - automatic behavioral pivoting on system failures
"""

from .drives import DriveModule, DriveState
from .habituation import ActionFingerprint, HabituationEntry, HabituationModule
from .mushroom_body import MushroomBody, MushroomBodyState, SparseCode
from .novelty import NoveltyModule, NoveltyState
from .rpe import PredictionError, RPEModule
from .task_compass import TaskCompass, TaskCompassState
from .valence import OutcomeSignals, ValenceModule
from .recovery import RecoveryState, evaluate_recovery

__all__ = [
    "TaskCompass",
    "TaskCompassState",
    "MushroomBody",
    "MushroomBodyState",
    "SparseCode",
    "ValenceModule",
    "OutcomeSignals",
    "NoveltyModule",
    "NoveltyState",
    "RPEModule",
    "PredictionError",
    "DriveModule",
    "DriveState",
    "HabituationModule",
    "HabituationEntry",
    "ActionFingerprint",
    "RecoveryState",
    "evaluate_recovery",
]
