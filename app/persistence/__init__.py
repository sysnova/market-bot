"""Public persistence engine API."""

from .database import create_database_engine, create_session_factory, normalize_database_url
from .models import Base, new_entity_id
from .repositories import (
    CheckpointRepository,
    EventPayloadConflictError,
    HealthRepository,
    InboxRepository,
    OutboxRepository,
    ProcessedEventRepository,
)
from .unit_of_work import PersistenceUnitOfWork

__all__ = [
    "Base",
    "CheckpointRepository",
    "EventPayloadConflictError",
    "HealthRepository",
    "InboxRepository",
    "OutboxRepository",
    "PersistenceUnitOfWork",
    "ProcessedEventRepository",
    "create_database_engine",
    "create_session_factory",
    "new_entity_id",
    "normalize_database_url",
]
