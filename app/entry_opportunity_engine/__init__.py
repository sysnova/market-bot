"""Versioned Entry Opportunity lifecycle engine."""

from .engine import (
    EntryOpportunityEngine,
    EntryOpportunityEngineV2,
    EntryOpportunityEngineV3,
    EntryOpportunityEngineV4,
    EntryOpportunityEngineV5,
)
from .memory import InMemoryEntryOpportunityStore
from .ports import EntryOpportunityStore

# Compatibility name for callers created before the engine became an assembly slot.
EntryOpportunityManager = EntryOpportunityEngine

__all__ = [
    "EntryOpportunityEngine",
    "EntryOpportunityEngineV2",
    "EntryOpportunityEngineV3",
    "EntryOpportunityEngineV4",
    "EntryOpportunityEngineV5",
    "EntryOpportunityManager",
    "EntryOpportunityStore",
    "InMemoryEntryOpportunityStore",
]
