"""Translate producer-specific decisions into the stable EntrySignal contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.alert_engine.confirmed import BuyMaturity, buy_maturity
from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    AlertKind,
    AnalysisHorizon,
    EntryMaturityLevel,
    EntrySignal,
    EntrySignalFamily,
    EntryWatchStatus,
    EntryWatchTransition,
    EventEnvelope,
    FusionState,
    FusionTransition,
    LocalAlert,
    entry_signal_subject,
    new_uuid7,
)

from .event_fanout import EventPublisher

_CORE_MATURITY = {
    BuyMaturity.TACTICAL_RECOVERY: EntryMaturityLevel.L1,
    BuyMaturity.SWING_CONFIRMED: EntryMaturityLevel.L2,
    BuyMaturity.HIGH_CONVICTION: EntryMaturityLevel.L3,
}
_ANALYTICAL_FAMILIES = {
    AlertKind.PATREON_CAPS_BUY: (EntrySignalFamily.PATREON_CAPS, "patreon-caps", "1.1.0"),
    AlertKind.LONG_PORTFOLIO_BUY: (
        EntrySignalFamily.LONG_PORTFOLIO,
        "long-portfolio",
        "1.0.0",
    ),
    AlertKind.PORTFOLIO_FLOW_BUY: (
        EntrySignalFamily.PORTFOLIO_FLOW,
        "portfolio-flow",
        "2.0.0",
    ),
}


def entry_signal_from_alert_watch(transition: EntryWatchTransition) -> EntrySignal | None:
    """Materialize Watcher's accepted initial and fully mature Core entries."""

    maturity = {
        EntryWatchStatus.EARLY_ENTRY: EntryMaturityLevel.L1,
        EntryWatchStatus.TRIGGERED: EntryMaturityLevel.L4,
    }.get(transition.status)
    if maturity is None:
        return None
    invalidation = transition.entry_invalidation or transition.invalidation
    targets = () if transition.entry_target is None else (transition.entry_target,)
    if maturity is EntryMaturityLevel.L1:
        zone_low = zone_high = transition.current_price
    else:
        zone_low, zone_high = transition.zone_low, transition.zone_high
    return EntrySignal(
        signal_id=transition.transition_id,
        family=EntrySignalFamily.CORE_ENTRY,
        maturity=maturity,
        symbol=transition.symbol,
        created_at=transition.occurred_at,
        setup_id=f"watch:{transition.watch_id}",
        entry_price=transition.current_price,
        horizons=transition.horizons,
        zone_low=zone_low,
        zone_high=zone_high,
        invalidation=invalidation,
        targets=targets,
        policy_id="core-entry",
        policy_version="1.0.0",
        reasons=transition.reasons,
        source_event_ids=(transition.transition_id, *transition.source_analysis_ids),
    )


def entry_signal_from_alert(alert: LocalAlert) -> EntrySignal | None:
    """Translate named decisions without exposing their kinds to Opportunity."""

    if alert.kind is AlertKind.ENTRY_WATCH:
        return None
    metrics = {item.name: item.value for item in alert.metrics}
    recovery = metrics.get("entry_signal_family") == EntrySignalFamily.CORE_RECOVERY.value
    maturity = buy_maturity(alert)
    family_spec = _ANALYTICAL_FAMILIES.get(alert.kind)
    if recovery:
        try:
            level = EntryMaturityLevel(str(metrics["entry_maturity"]))
        except KeyError, ValueError:
            return None
        if level not in {
            EntryMaturityLevel.L1,
            EntryMaturityLevel.L2,
            EntryMaturityLevel.L3,
            EntryMaturityLevel.L4,
        }:
            return None
        family = EntrySignalFamily.CORE_RECOVERY
        policy_id = "core-recovery"
        default_policy_version = "1.1.0"
    elif maturity is not None and maturity in _CORE_MATURITY:
        family = EntrySignalFamily.CORE_ENTRY
        level = _CORE_MATURITY[maturity]
        policy_id = "core-entry"
        default_policy_version = "1.0.0"
    elif family_spec is not None:
        family, policy_id, default_policy_version = family_spec
        level = None
    else:
        return None

    policy_version = _policy_version(
        metrics,
        default=default_policy_version,
    )
    price = _decimal_metric(metrics, "entry_price", "current_price", "reference_price")
    if price is None:
        return None
    zone_low = _decimal_metric(metrics, "buy_zone_low", "entry_zone_low", "zone_low")
    zone_high = _decimal_metric(metrics, "buy_zone_high", "entry_zone_high", "zone_high")
    invalidation = _decimal_metric(metrics, "invalidation", "stop_price")
    if zone_low is None or zone_high is None or invalidation is None:
        zone_low = zone_high = invalidation = None
    targets = tuple(
        value
        for name in ("target", "target_price", "target_1", "target_2", "target_3")
        if (value := _decimal_metric(metrics, name)) is not None
    )
    return EntrySignal(
        signal_id=alert.alert_id if recovery else new_uuid7(),
        family=family,
        maturity=level,
        symbol=alert.symbol,
        created_at=alert.created_at,
        setup_id=(
            str(metrics.get("entry_setup_id", alert.deduplication_key))
            if recovery
            else alert.deduplication_key
        ),
        entry_price=price,
        horizons=alert.horizons,
        zone_low=zone_low,
        zone_high=zone_high,
        invalidation=invalidation,
        targets=tuple(dict.fromkeys(targets)),
        policy_id=policy_id,
        policy_version=policy_version,
        reasons=alert.reasons,
        source_event_ids=(alert.alert_id, *alert.component_analysis_ids),
    )


def entry_signal_from_fusion(transition: FusionTransition) -> EntrySignal | None:
    """Expose holdings-only Fusion decisions without assigning a core L-level."""

    if transition.state not in {FusionState.BUY_CONFIRMED, FusionState.RECOVERY_CONFIRMED}:
        return None
    if (
        transition.entry_price is None
        or transition.trigger_price is None
        or transition.invalidation is None
    ):
        return None
    zone_low = min(transition.entry_price, transition.trigger_price)
    zone_high = max(transition.entry_price, transition.trigger_price)
    targets = () if transition.target_price is None else (transition.target_price,)
    return EntrySignal(
        family=EntrySignalFamily.SIGNAL_FUSION,
        symbol=transition.symbol,
        created_at=transition.occurred_at,
        setup_id=f"fusion:{transition.assessment_id}",
        entry_price=transition.entry_price,
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        zone_low=zone_low,
        zone_high=zone_high,
        invalidation=transition.invalidation,
        targets=targets,
        policy_id="signal-fusion",
        policy_version="1.0.0",
        reasons=transition.reasons,
        source_event_ids=(transition.transition_id, transition.assessment_id),
    )


async def publish_entry_signal(
    publisher: EventPublisher,
    signal: EntrySignal,
    *,
    source: str,
) -> None:
    """Publish the stable signal with its own event and subject contract."""

    await publisher.publish(
        entry_signal_subject(signal.family, signal.symbol),
        EventEnvelope(
            event_id=signal.signal_id,
            event_type=ENTRY_SIGNAL_EVENT,
            occurred_at=signal.created_at,
            source=source,
            subject=signal.symbol,
            payload=signal,
            causation_id=signal.source_event_ids[0] if signal.source_event_ids else None,
        ),
    )


def _decimal_metric(metrics: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        value = metrics.get(name)
        if isinstance(value, Decimal) and value > 0:
            return value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return Decimal(value)
    return None


def _policy_version(metrics: dict[str, Any], *, default: str) -> str:
    for name in (
        "entry_confirmation_rule_version",
        "alert_strategy_version",
        "patreon_caps_rule_version",
        "long_portfolio_rule_version",
        "portfolio_flow_rule_version",
        "entry_setup_policy_version",
        "strategy_version",
    ):
        value = metrics.get(name)
        if isinstance(value, str) and _is_semver(value):
            return value
    return default


def _is_semver(value: str) -> bool:
    components = value.split(".")
    return len(components) == 3 and all(item.isdigit() for item in components)
