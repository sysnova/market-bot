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
    "EntryOpportunityManager",
    "EntryOpportunityStore",
    "InMemoryEntryOpportunityStore",
]
