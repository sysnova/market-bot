"""Public surface of the append-only audit engine."""

from .errors import (
    AuditEngineError,
    CorruptAuditLogError,
    InvalidAuditEventError,
    WriterAlreadyActiveError,
)
from .service import AuditConfirmation, AuditService, EventBus, Subscription
from .store import AuditLog, AuditReceipt, AuditStream

__all__ = [
    "AuditConfirmation",
    "AuditEngineError",
    "AuditLog",
    "AuditReceipt",
    "AuditService",
    "AuditStream",
    "CorruptAuditLogError",
    "EventBus",
    "InvalidAuditEventError",
    "Subscription",
    "WriterAlreadyActiveError",
]
