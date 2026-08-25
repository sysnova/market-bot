"""Stable health metadata describing how every logical engine selects symbols."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UniversePolicyDetails:
    universe_policy: str
    warmup_policy: str

    def as_health_details(self) -> dict[str, str]:
        return {
            "universe_policy": self.universe_policy,
            "warmup_policy": self.warmup_policy,
        }


CORE_DYNAMIC = UniversePolicyDetails(
    universe_policy="active-watchlist-plus-positive-holdings",
    warmup_policy="per-symbol-history-before-universe-activation",
)
DERIVED_CORE = UniversePolicyDetails(
    universe_policy="derived-from-core-analysis-and-entry-events",
    warmup_policy="source-contract-replay-and-durable-state-restore",
)
HOLDINGS_ONLY = UniversePolicyDetails(
    universe_policy="positive-holdings-only",
    warmup_policy="startup-holdings-snapshot-then-latest-analysis-replay",
)
TAGGED_PORTFOLIO = UniversePolicyDetails(
    universe_policy="active-watchlist-tagged-port-ytd",
    warmup_policy="startup-tagged-allocation-snapshot",
)
FIXED_ROTATION = UniversePolicyDetails(
    universe_policy="fixed-configured-sector-profiles-and-proxies",
    warmup_policy="configured-symbol-history-before-rotation-run",
)
REGISTERED_WATCHLIST = UniversePolicyDetails(
    universe_policy="registered-active-watchlist",
    warmup_policy="scheduled-or-on-demand-provider-refresh",
)
PATREON_CORE = UniversePolicyDetails(
    universe_policy="core-snapshot-plus-configured-macro-symbols",
    warmup_policy="startup-market-history-and-latest-analysis-replay",
)
PORTFOLIO_FLOW = UniversePolicyDetails(
    universe_policy="positive-holdings-with-live-trades-and-quotes",
    warmup_policy="startup-holdings-snapshot-then-live-flow-window",
)
VOLUME_STRUCTURE = UniversePolicyDetails(
    universe_policy="active-watchlist-plus-positive-holdings",
    warmup_policy="completed-weekly-history-before-divergence-evaluation",
)
OPTIONS_GAMMA = UniversePolicyDetails(
    universe_policy="active-watchlist-plus-positive-holdings",
    warmup_policy="startup-universe-snapshot-then-periodic-options-refresh",
)
MICROSTRUCTURE = UniversePolicyDetails(
    universe_policy="fixed-order-flow-strategy-symbols-only",
    warmup_policy="live-quote-and-trade-window-before-state-publication",
)
LEVERAGED_THESIS = UniversePolicyDetails(
    universe_policy="fixed-underlying-and-directional-instrument-pairs",
    warmup_policy="latest-intraday-and-order-flow-assessments-from-nats",
)
NEWS_INTELLIGENCE = UniversePolicyDetails(
    universe_policy="active-watchlist-plus-positive-holdings",
    warmup_policy="durable-article-deduplication-before-llm-classification",
)
SWING_CHANNEL_4H = UniversePolicyDetails(
    universe_policy="active-watchlist-plus-positive-holdings",
    warmup_policy="completed-rth-15m-history-aggregated-into-4h-channel-bars",
)
GERI_4H = UniversePolicyDetails(
    universe_policy="active-watchlist-plus-positive-holdings",
    warmup_policy="completed-rth-15m-history-reconstructed-as-horizontal-4h-levels",
)
SWING_TRADE = UniversePolicyDetails(
    universe_policy="active-watchlist-only-excluding-holdings",
    warmup_policy="120-completed-daily-bars-plus-latest-geri-replay-before-15m-evaluation",
)

ENGINE_UNIVERSE_POLICIES: dict[str, UniversePolicyDetails] = {
    "long-term": CORE_DYNAMIC,
    "swing": CORE_DYNAMIC,
    "swing-channel-4h": SWING_CHANNEL_4H,
    "4hgeri": GERI_4H,
    "swing-trade": SWING_TRADE,
    "intraday": CORE_DYNAMIC,
    "order-flow": MICROSTRUCTURE,
    "leveraged-thesis": LEVERAGED_THESIS,
    "volume-structure": VOLUME_STRUCTURE,
    "options-gamma": OPTIONS_GAMMA,
    "news-intelligence": NEWS_INTELLIGENCE,
    "entry-watcher": DERIVED_CORE,
    "entry-opportunity": DERIVED_CORE,
    "entry-recovery": DERIVED_CORE,
    "alert": DERIVED_CORE,
    "market-rotation": FIXED_ROTATION,
    "portfolio-flow": PORTFOLIO_FLOW,
    "long-portfolio": TAGGED_PORTFOLIO,
    "patreon-caps": PATREON_CORE,
    "elliott-wave": HOLDINGS_ONLY,
    "support-confirmation": CORE_DYNAMIC,
    "signal-fusion": HOLDINGS_ONLY,
    "dilution-sec": REGISTERED_WATCHLIST,
    "peter-lynch": REGISTERED_WATCHLIST,
}


def universe_health_details(service: str) -> dict[str, str]:
    logical = service.strip().lower().split("-v", 1)[0]
    try:
        return ENGINE_UNIVERSE_POLICIES[logical].as_health_details()
    except KeyError as error:
        raise ValueError(f"unknown engine universe policy: {service}") from error
