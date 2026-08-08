"""Stable external subscription catalog for MarketBot event families."""

from __future__ import annotations

from dataclasses import dataclass

from .subjects import subject_matches, validate_subscription_subject


@dataclass(frozen=True, slots=True)
class SubjectRoute:
    """One server-side subject filter with optional envelope source filtering."""

    subject: str
    source_prefixes: tuple[str, ...] = ()

    def accepts(self, subject: str, source: str | None) -> bool:
        if not subject_matches(self.subject, subject):
            return False
        if not self.source_prefixes:
            return True
        return source is not None and source.startswith(self.source_prefixes)


@dataclass(frozen=True, slots=True)
class FilterPlan:
    """Resolved filters used by both JetStream and the decoded envelope gate."""

    routes: tuple[SubjectRoute, ...]

    @property
    def subjects(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(route.subject for route in self.routes))

    def accepts(self, subject: str, source: str | None) -> bool:
        return any(route.accepts(subject, source) for route in self.routes)


ENGINE_ROUTES: dict[str, tuple[SubjectRoute, ...]] = {
    "long-term": (SubjectRoute("marketbot.v1.analysis.result.LONG_TERM.>"),),
    "swing": (SubjectRoute("marketbot.v1.analysis.result.SWING.>"),),
    "intraday": (SubjectRoute("marketbot.v1.analysis.result.INTRADAY.>"),),
    "dilution-sec": (SubjectRoute("marketbot.v1.analysis.result.DILUTION.>"),),
    "entry-watcher": (SubjectRoute("marketbot.v1.entry-watch.transition.>"),),
    "entry-opportunity": (SubjectRoute("marketbot.v1.entry-opportunity.transition.>"),),
    "alert": (SubjectRoute("marketbot.v1.alert.local.>", ("alert-engine",)),),
    "market-rotation": (SubjectRoute("marketbot.v1.rotation.result"),),
    "portfolio-flow": (
        SubjectRoute("marketbot.v1.alert.local.>", ("portfolio-flow-engine",)),
    ),
    "long-portfolio": (
        SubjectRoute("marketbot.v1.alert.local.>", ("long-portfolio-engine",)),
    ),
    "patreon-caps": (
        SubjectRoute("marketbot.v1.patreon-caps.assessment.>"),
        SubjectRoute("marketbot.v1.patreon-caps.transition.>"),
    ),
    "elliott-wave": (SubjectRoute("marketbot.v1.elliott-wave.assessment.>"),),
    "support-confirmation": (
        SubjectRoute("marketbot.v1.support-confirmation.assessment.>"),
        SubjectRoute("marketbot.v1.support-confirmation.transition.>"),
    ),
    "signal-fusion": (
        SubjectRoute("marketbot.v1.signal-fusion.assessment.>"),
        SubjectRoute("marketbot.v1.signal-fusion.transition.>"),
        SubjectRoute("marketbot.v1.signal-fusion.buy-confirmed.>"),
        SubjectRoute("marketbot.v1.signal-fusion.recovery-confirmed.>"),
    ),
    "market-bars": (SubjectRoute("marketbot.v1.market.bar.>"),),
    "service-health": (SubjectRoute("marketbot.v1.service.health.>"),),
}


def resolve_filters(
    *,
    engines: tuple[str, ...] = (),
    subjects: tuple[str, ...] = (),
    all_messages: bool = False,
) -> FilterPlan:
    """Resolve friendly engine names and raw NATS filters into one plan."""

    if all_messages and (engines or subjects):
        raise ValueError("all_messages cannot be combined with engine or subject filters")
    if all_messages:
        return FilterPlan(
            routes=(SubjectRoute("marketbot.v1.>"), SubjectRoute("marketbot.dlq"))
        )
    if not engines and not subjects:
        raise ValueError("at least one engine, subject, or all_messages is required")

    routes: list[SubjectRoute] = []
    for engine in engines:
        normalized = engine.strip().lower()
        try:
            routes.extend(ENGINE_ROUTES[normalized])
        except KeyError as error:
            available = ", ".join(sorted(ENGINE_ROUTES))
            raise ValueError(f"unknown engine {engine!r}; available: {available}") from error
    for subject in subjects:
        validate_subscription_subject(subject)
        routes.append(SubjectRoute(subject))
    return FilterPlan(routes=tuple(dict.fromkeys(routes)))
