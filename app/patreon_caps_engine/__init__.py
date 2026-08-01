"""PatreonCaps technical and macro shadow engine."""

from .config import default_policy, load_patreon_caps_policy
from .engine import PatreonCapsEngine
from .lesson import evaluate_lesson
from .models import (
    LessonAssessment,
    PatreonCapsContext,
    PatreonCapsEvaluation,
    PatreonCapsPolicy,
    PatreonCapsWatch,
    ReplayOutcome,
    SupportLevel,
    SupportZone,
    TrancheSizing,
)
from .replay import replay_outcomes

__all__ = [
    "LessonAssessment",
    "PatreonCapsContext",
    "PatreonCapsEngine",
    "PatreonCapsEvaluation",
    "PatreonCapsPolicy",
    "PatreonCapsWatch",
    "ReplayOutcome",
    "SupportLevel",
    "SupportZone",
    "TrancheSizing",
    "default_policy",
    "evaluate_lesson",
    "load_patreon_caps_policy",
    "replay_outcomes",
]
