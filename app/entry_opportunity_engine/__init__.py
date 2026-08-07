"""Versioned Entry Opportunity lifecycle engine."""

from .engine import EntryOpportunityEngine
from .memory import InMemoryEntryOpportunityStore
from .ports import EntryOpportunityStore

# Compatibility name for callers created before the engine became an assembly slot.
EntryOpportunityManager = EntryOpportunityEngine

__all__ = [
    "EntryOpportunityEngine",
    "EntryOpportunityManager",
    "EntryOpportunityStore",
    "InMemoryEntryOpportunityStore",
]
