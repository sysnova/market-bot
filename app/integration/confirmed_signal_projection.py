"""Project final EntrySignal decisions into the focused operator buy view."""

from __future__ import annotations

from dataclasses import dataclass

from app.alert_engine.confirmed import BuyMaturity
from app.contracts import (
    EntryMaturityLevel,
    EntrySignal,
    EntrySignalFamily,
    GeriCountertrendMaturity,
    SwingTradeMaturity,
)

_RESET_STYLE = "\x1b[0m"
_CORE_MATURITY = {
    EntryMaturityLevel.L1: (BuyMaturity.TACTICAL_RECOVERY, "\x1b[1;30;103m"),
    EntryMaturityLevel.L2: (BuyMaturity.SWING_CONFIRMED, "\x1b[1;97;44m"),
    EntryMaturityLevel.L3: (BuyMaturity.HIGH_CONVICTION, "\x1b[1;30;102m"),
    EntryMaturityLevel.L4: (BuyMaturity.FULLY_MATURED, "\x1b[1;97;45m"),
}
_CORE_LABELS = {
    EntrySignalFamily.CORE_ENTRY: "CORE ENTRY",
    EntrySignalFamily.CORE_RECOVERY: "CORE RECOVERY",
}
_FINAL_ANALYTICAL_LABELS = {
    EntrySignalFamily.PATREON_CAPS: "PATREON CAPS CONFIRMED",
    EntrySignalFamily.LONG_PORTFOLIO: "LONG PORTFOLIO BUY",
    EntrySignalFamily.SIGNAL_FUSION: "SIGNAL FUSION CONFIRMED",
}
_ANALYTICAL_STYLE = "\x1b[1;97;45m"
_NEWS_RISK_STYLE = "\x1b[1;97;41m"
_GERI_CONFIRMED_MATURITY = {
    GeriCountertrendMaturity.CT2: BuyMaturity.SWING_CONFIRMED,
    GeriCountertrendMaturity.CT3: BuyMaturity.HIGH_CONVICTION,
    GeriCountertrendMaturity.CT4: BuyMaturity.FULLY_MATURED,
}


@dataclass(frozen=True, slots=True)
class ConfirmedSignalProjection:
    """One final operator notification derived from the stable signal contract."""

    text: str
    sound_maturity: BuyMaturity | None


def project_confirmed_signal(
    signal: EntrySignal,
    *,
    color: bool = True,
) -> ConfirmedSignalProjection | None:
    """Render only final buy decisions; omit watches and manual Flow alarms."""

    sound_maturity: BuyMaturity | None = None
    if signal.family in _CORE_LABELS:
        maturity = signal.maturity
        if maturity is None:
            return None
        maturity_spec = _CORE_MATURITY.get(maturity)
        if maturity_spec is None:
            return None
        sound_maturity, style = maturity_spec
        label = f"{_CORE_LABELS[signal.family]} {maturity.value}"
    elif signal.family is EntrySignalFamily.SWING_TRADE:
        swing_trade_maturity = signal.swing_trade_maturity
        if swing_trade_maturity is None or swing_trade_maturity not in {
            SwingTradeMaturity.ST3,
            SwingTradeMaturity.ST4,
        }:
            return None
        label = f"SWING TRADE {swing_trade_maturity.value}"
        style = _ANALYTICAL_STYLE
    elif signal.family is EntrySignalFamily.GERI_COUNTERTREND:
        countertrend_maturity = signal.countertrend_maturity
        if countertrend_maturity is None:
            return None
        sound_maturity = _GERI_CONFIRMED_MATURITY.get(countertrend_maturity)
        if sound_maturity is None:
            return None
        label = f"GERI REACTION {countertrend_maturity.value}"
        style = _ANALYTICAL_STYLE
    else:
        label = _FINAL_ANALYTICAL_LABELS.get(signal.family)
        if label is None:
            return None
        style = _ANALYTICAL_STYLE

    news_risk = "news_risk_active:red_alert" in signal.reasons
    if news_risk:
        style = _NEWS_RISK_STYLE
        label = f"{label} | NEWS RISK"
    banner = f"{signal.symbol} | {label} | PX ${signal.entry_price}"
    if color:
        banner = f"{style} {banner} {_RESET_STYLE}"
    levels = _format_levels(signal)
    return ConfirmedSignalProjection(
        text="\n".join(
            (
                banner,
                f"[CONFIRMED] {signal.family.value} policy="
                f"{signal.policy_id}@{signal.policy_version}",
                f"  {levels}",
                f"  Reasons: {'; '.join(signal.reasons)}",
            )
        ),
        sound_maturity=sound_maturity,
    )


def _format_levels(signal: EntrySignal) -> str:
    parts = [f"Entry {signal.entry_price}"]
    if signal.zone_low is not None and signal.zone_high is not None:
        parts.append(f"Zone {signal.zone_low}-{signal.zone_high}")
    if signal.invalidation is not None:
        parts.append(f"Invalidation {signal.invalidation}")
    if signal.targets:
        parts.append(f"Targets {','.join(str(value) for value in signal.targets)}")
    return " | ".join(parts)
