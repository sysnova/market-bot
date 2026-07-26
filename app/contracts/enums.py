"""Stable string enumerations shared across process boundaries."""

# Ruff's S105 heuristic mistakes domain statuses containing PASS for secrets.
# ruff: noqa: S105

from enum import StrEnum


class ContractEnum(StrEnum):
    pass


class MarketSession(ContractEnum):
    PRE_MARKET = "PRE_MARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"
    CONTINUOUS = "CONTINUOUS"


class StrategyMode(ContractEnum):
    PRIMARY = "PRIMARY"
    SHADOW = "SHADOW"
    RESEARCH = "RESEARCH"
    DISABLED = "DISABLED"


class RuleType(ContractEnum):
    FILTER = "FILTER"
    SIGNAL = "SIGNAL"
    CONFIRMATION = "CONFIRMATION"
    SCORING = "SCORING"
    RISK = "RISK"
    SIZING = "SIZING"
    EXIT = "EXIT"


class RuleStatus(ContractEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"


class RuleLifecycleStatus(ContractEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PAPER = "PAPER"
    APPROVED = "APPROVED"
    DEPRECATED = "DEPRECATED"


class RuleTraceStatus(ContractEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"
    SKIPPED_DEPENDENCY = "SKIPPED_DEPENDENCY"


class DependencyPolicy(ContractEnum):
    REQUIRE_PASS = "REQUIRE_PASS"
    REQUIRE_COMPLETION = "REQUIRE_COMPLETION"


class DecisionOutcome(ContractEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NO_DECISION = "NO_DECISION"
    ERROR = "ERROR"


class PatternDirection(ContractEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class TradeSide(ContractEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class AlertSeverity(ContractEnum):
    INFO = "INFO"
    WATCH = "WATCH"
    ACTION = "ACTION"
    CRITICAL = "CRITICAL"


class ServiceStatus(ContractEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"
