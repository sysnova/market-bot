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


class BarTimeframe(ContractEnum):
    MINUTE_1 = "1Min"
    MINUTE_5 = "5Min"
    MINUTE_15 = "15Min"
    HOUR_1 = "1Hour"
    DAY_1 = "1Day"
    WEEK_1 = "1Week"


class AnalysisHorizon(ContractEnum):
    LONG_TERM = "LONG_TERM"
    DILUTION = "DILUTION"
    SWING = "SWING"
    INTRADAY = "INTRADAY"


class AnalysisVerdict(ContractEnum):
    FAVORABLE = "FAVORABLE"
    WATCH = "WATCH"
    CAUTION = "CAUTION"
    AVOID = "AVOID"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class TradeSide(ContractEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class AlertSeverity(ContractEnum):
    INFO = "INFO"
    WATCH = "WATCH"
    ACTION = "ACTION"
    CRITICAL = "CRITICAL"


class AlertKind(ContractEnum):
    CONSENSUS = "CONSENSUS"
    LONG_BUY_ZONE = "LONG_BUY_ZONE"
    SWING_SETUP = "SWING_SETUP"
    ENTRY_CONFIRMED = "ENTRY_CONFIRMED"
    HIGH_CONVICTION_BUY = "HIGH_CONVICTION_BUY"
    BEARISH_CONSENSUS = "BEARISH_CONSENSUS"
    SEC_WARNING = "SEC_WARNING"
    ENTRY_WATCH = "ENTRY_WATCH"
    PORTFOLIO_PROTECT = "PORTFOLIO_PROTECT"
    LONG_PORTFOLIO_BUY = "LONG_PORTFOLIO_BUY"
    PATREON_CAPS_WATCH = "PATREON_CAPS_WATCH"
    PATREON_CAPS_BUY = "PATREON_CAPS_BUY"
    PATREON_CAPS_INVALIDATED = "PATREON_CAPS_INVALIDATED"


class EntryWatchStatus(ContractEnum):
    ARMED = "ARMED"
    IN_ZONE = "IN_ZONE"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class PatreonCapsState(ContractEnum):
    WATCH_ZONE = "WATCH_ZONE"
    SUPPORT_TEST = "SUPPORT_TEST"
    CONFIRMED_V = "CONFIRMED_V"
    CONFIRMED_BASE = "CONFIRMED_BASE"
    IMPULSE_RETEST = "IMPULSE_RETEST"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class WavePhase(ContractEnum):
    """Observable Elliott hypothesis; never an execution instruction."""

    WAVE_2_ENDING = "WAVE_2_ENDING"
    WAVE_3_ACTIVE = "WAVE_3_ACTIVE"
    WAVE_4_ENDING = "WAVE_4_ENDING"
    WAVE_5_ACTIVE = "WAVE_5_ACTIVE"
    ABC_INCOMPLETE = "ABC_INCOMPLETE"
    UNRESOLVED = "UNRESOLVED"


class SupportState(ContractEnum):
    """Lifecycle of an independently observed higher-timeframe support thesis."""

    NO_KEY_SUPPORT = "NO_KEY_SUPPORT"
    WATCH_KEY_SUPPORT = "WATCH_KEY_SUPPORT"
    FIRST_TOUCH = "FIRST_TOUCH"
    REACTION_CONFIRMED = "REACTION_CONFIRMED"
    BASE_BUILDING = "BASE_BUILDING"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    RECLAIMED = "RECLAIMED"
    STRUCTURE_CONFIRMED = "STRUCTURE_CONFIRMED"
    RETEST_CONFIRMED = "RETEST_CONFIRMED"
    B_WAVE_RISK = "B_WAVE_RISK"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class SupportConfirmationType(ContractEnum):
    NONE = "NONE"
    V_RECOVERY = "V_RECOVERY"
    BASE_BREAKOUT = "BASE_BREAKOUT"
    SWEEP_RECLAIM = "SWEEP_RECLAIM"


class MacroRegime(ContractEnum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"
    SHOCK = "SHOCK"
    UNKNOWN = "UNKNOWN"


class ServiceStatus(ContractEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"
