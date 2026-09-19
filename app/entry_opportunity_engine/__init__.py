"""Versioned Entry Opportunity lifecycle engine."""

from .engine import (
    EntryOpportunityEngine,
    EntryOpportunityEngineV2,
    EntryOpportunityEngineV3,
    EntryOpportunityEngineV4,
    EntryOpportunityEngineV5,
    EntryOpportunityEngineV6,
    EntryOpportunityEngineV7,
    EntryOpportunityEngineV8,
    EntryOpportunityEngineV9,
    EntryOpportunityEngineV10,
    EntryOpportunityEngineV11,
)
from .memory import InMemoryEntryOpportunityStore
from .ports import EntryOpportunityStore
from .v12 import EntryOpportunityEngineV12
from .v13 import EntryOpportunityEngineV13
from .v14 import EntryOpportunityEngineV14
from .v16 import EntryOpportunityEngineV16
from .v17 import EntryOpportunityEngineV17
from .v18 import EntryOpportunityEngineV18
from .v19 import EntryOpportunityEngineV19
from .v20 import EntryOpportunityEngineV20
from .v21 import EntryOpportunityEngineV21
from .v22 import EntryOpportunityEngineV22
from .v23 import EntryOpportunityEngineV23

# Compatibility name for callers created before the engine became an assembly slot.
EntryOpportunityManager = EntryOpportunityEngine


__all__ = [
    "EntryOpportunityEngine",
    "EntryOpportunityEngineV2",
    "EntryOpportunityEngineV3",
    "EntryOpportunityEngineV4",
    "EntryOpportunityEngineV5",
    "EntryOpportunityEngineV6",
    "EntryOpportunityEngineV7",
    "EntryOpportunityEngineV8",
    "EntryOpportunityEngineV9",
    "EntryOpportunityEngineV10",
    "EntryOpportunityEngineV11",
    "EntryOpportunityEngineV12",
    "EntryOpportunityEngineV13",
    "EntryOpportunityEngineV14",
    "EntryOpportunityEngineV16",
    "EntryOpportunityEngineV17",
    "EntryOpportunityEngineV18",
    "EntryOpportunityEngineV19",
    "EntryOpportunityEngineV20",
    "EntryOpportunityEngineV21",
    "EntryOpportunityEngineV22",
    "EntryOpportunityEngineV23",
    "EntryOpportunityManager",
    "EntryOpportunityStore",
    "InMemoryEntryOpportunityStore",
]
