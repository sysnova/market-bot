"""Stable string enumerations shared across process boundaries."""

# Ruff's S105 heuristic mistakes domain statuses containing PASS for secrets.
# ruff: noqa: S105

from enum import StrEnum
from typing import Self, cast


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
    CANDIDATE = "CANDIDATE"
    RESEARCH = "RESEARCH"
    DISABLED = "DISABLED"

    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        # SHADOW was renamed to CANDIDATE; durable events may still carry the old value.
        if value == "SHADOW":
            return cast("Self", cls.CANDIDATE)
        return None


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
    HOUR_4 = "4Hour"
    DAY_1 = "1Day"
    WEEK_1 = "1Week"


class AnalysisHorizon(ContractEnum):
    LONG_TERM = "LONG_TERM"
    DILUTION = "DILUTION"
    SWING = "SWING"
    INTRADAY = "INTRADAY"
    VOLUME_STRUCTURE = "VOLUME_STRUCTURE"
    OPTIONS_GAMMA = "OPTIONS_GAMMA"
    NEWS = "NEWS"


class SwingChannelMaturity(ContractEnum):
    """Independent 4h channel maturity; it never replaces core L1-L4 state."""

    ARMED = "ARMED"
    IN_ZONE_4H = "IN_ZONE_4H"
    L2_4H = "L2_4H"
    L3 = "L3"
    L4 = "L4"
    INVALIDATED = "INVALIDATED"


class GeriLevelKind(ContractEnum):
    """Alternating horizontal structural level used by the 4HGERI model."""

    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class GeriMaturity(ContractEnum):
    """Independent 4HGERI lifecycle; core Opportunity maturity is unchanged."""

    BUILDING = "BUILDING"
    ARMED = "ARMED"
    IN_ZONE_4H = "IN_ZONE_4H"
    L2_4H = "L2_4H"
    L3 = "L3"
    L4 = "L4"
    INVALIDATED = "INVALIDATED"


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
    EARLY_INTRADAY_WITHOUT_CONFIRMATION = "EARLY_INTRADAY_WITHOUT_CONFIRMATION"
    ENTRY_CONFIRMED = "ENTRY_CONFIRMED"
    HIGH_CONVICTION_BUY = "HIGH_CONVICTION_BUY"
    BEARISH_CONSENSUS = "BEARISH_CONSENSUS"
    SEC_WARNING = "SEC_WARNING"
    ENTRY_WATCH = "ENTRY_WATCH"
    ENTRY_OPPORTUNITY_PROGRESS = "ENTRY_OPPORTUNITY_PROGRESS"
    ENTRY_OPPORTUNITY_CLOSED = "ENTRY_OPPORTUNITY_CLOSED"
    PORTFOLIO_PROTECT = "PORTFOLIO_PROTECT"
    PORTFOLIO_FLOW_BUY = "PORTFOLIO_FLOW_BUY"
    LONG_PORTFOLIO_BUY = "LONG_PORTFOLIO_BUY"
    PATREON_CAPS_WATCH = "PATREON_CAPS_WATCH"
    PATREON_CAPS_BUY = "PATREON_CAPS_BUY"
    PATREON_CAPS_INVALIDATED = "PATREON_CAPS_INVALIDATED"
    OBV_BULLISH_DIVERGENCE = "OBV_BULLISH_DIVERGENCE"
    NEWS_RISK = "NEWS_RISK"


class EntryWatchStatus(ContractEnum):
    ARMED = "ARMED"
    IN_ZONE = "IN_ZONE"
    EARLY_ENTRY = "EARLY_ENTRY"
    IMPULSE_EXTENDED = "IMPULSE_EXTENDED"
    TRIGGERED = "TRIGGERED"
    POLICY_INELIGIBLE = "POLICY_INELIGIBLE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class EntryOpportunityStatus(ContractEnum):
    """Aggregate lifecycle of one durable ticker opportunity."""

    ARMED = "ARMED"
    IN_ZONE = "IN_ZONE"
    CONFIRMING = "CONFIRMING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class EntryMaturityLevel(ContractEnum):
    """Highest evidence checkpoint reached without replacing the original thesis."""

    ARMED = "ARMED"
    IN_ZONE = "IN_ZONE"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class EntrySignalFamily(ContractEnum):
    """Stable strategy family; deliberately independent from producer identity."""

    CORE_ENTRY = "CORE_ENTRY"
    CORE_RECOVERY = "CORE_RECOVERY"
    PATREON_CAPS = "PATREON_CAPS"
    LONG_PORTFOLIO = "LONG_PORTFOLIO"
    SIGNAL_FUSION = "SIGNAL_FUSION"
    PORTFOLIO_FLOW = "PORTFOLIO_FLOW"


class EntryLegStatus(ContractEnum):
    """Paper-trade state of one independently managed horizon."""

    WATCHING = "WATCHING"
    OPEN = "OPEN"
    TARGET_HIT = "TARGET_HIT"
    INVALIDATED = "INVALIDATED"
    SESSION_CLOSED = "SESSION_CLOSED"
    THESIS_BROKEN = "THESIS_BROKEN"
    EXPIRED = "EXPIRED"
    TIME_EXIT = "TIME_EXIT"


class EntryCheckpointStatus(ContractEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class EntryCloseReason(ContractEnum):
    POLICY_INELIGIBLE = "POLICY_INELIGIBLE"
    ORIGINAL_THESIS_INVALIDATED = "ORIGINAL_THESIS_INVALIDATED"
    EXPIRED = "EXPIRED"
    UNIVERSE_REMOVED = "UNIVERSE_REMOVED"
    ALL_HORIZONS_CLOSED = "ALL_HORIZONS_CLOSED"


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
    NO_NEARBY_SUPPORT = "NO_NEARBY_SUPPORT"
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


class FusionState(ContractEnum):
    """Lifecycle of a cross-engine entry decision."""

    INCOMPLETE = "INCOMPLETE"
    OBSERVING = "OBSERVING"
    ARMED = "ARMED"
    RECOVERY_CONFIRMED = "RECOVERY_CONFIRMED"
    BUY_CONFIRMED = "BUY_CONFIRMED"
    VETOED = "VETOED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


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
