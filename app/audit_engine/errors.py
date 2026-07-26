"""Errors raised while protecting the append-only audit boundary."""


class AuditEngineError(Exception):
    """Base class for audit-engine failures."""


class CorruptAuditLogError(AuditEngineError):
    """Raised when a completed NDJSON record cannot be reconstructed."""


class WriterAlreadyActiveError(AuditEngineError):
    """Raised when a second in-process writer targets the same audit file."""


class InvalidAuditEventError(AuditEngineError):
    """Raised when an envelope does not carry valid audit routing metadata."""
