"""Backward-compatible namespace for :mod:`marketbot_connector`."""

from marketbot_connector import (
    ENGINE_ROUTES,
    ConnectorConfig,
    ConnectorHandler,
    ConnectorMessage,
    EventEnvelope,
    FilterPlan,
    MarketBotSubscriber,
    MarketSession,
    NamedValue,
    SubjectRoute,
    parse_start_at,
    reset_durable_consumer,
    resolve_filters,
    validate_durable_name,
)

__all__ = [
    "ENGINE_ROUTES",
    "ConnectorConfig",
    "ConnectorHandler",
    "ConnectorMessage",
    "EventEnvelope",
    "FilterPlan",
    "MarketBotSubscriber",
    "MarketSession",
    "NamedValue",
    "SubjectRoute",
    "parse_start_at",
    "reset_durable_consumer",
    "resolve_filters",
    "validate_durable_name",
]
