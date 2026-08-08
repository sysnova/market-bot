from __future__ import annotations

import pytest
from marketbot_connector import ENGINE_ROUTES, resolve_filters


def test_engine_catalog_resolves_server_and_source_filters() -> None:
    plan = resolve_filters(engines=("swing", "portfolio-flow"))

    assert plan.subjects == (
        "marketbot.v1.analysis.result.SWING.>",
        "marketbot.v1.alert.local.>",
    )
    assert plan.accepts("marketbot.v1.analysis.result.SWING.AAPL", "swing-v3")
    assert plan.accepts("marketbot.v1.alert.local.ACTION.AAPL", "portfolio-flow-engine")
    assert not plan.accepts("marketbot.v1.alert.local.ACTION.AAPL", "alert-engine")


def test_all_messages_includes_domain_events_and_dlq() -> None:
    plan = resolve_filters(all_messages=True)

    assert plan.subjects == ("marketbot.v1.>", "marketbot.dlq")
    assert plan.accepts("marketbot.v1.market.bar.1Min.AAPL", "marketbot-aggregator")
    assert plan.accepts("marketbot.dlq", None)


def test_filter_selection_is_explicit_and_validated() -> None:
    with pytest.raises(ValueError, match="at least one"):
        resolve_filters()
    with pytest.raises(ValueError, match="cannot be combined"):
        resolve_filters(engines=("swing",), all_messages=True)
    with pytest.raises(ValueError, match="unknown engine"):
        resolve_filters(engines=("missing",))
    with pytest.raises(ValueError, match="wildcard"):
        resolve_filters(subjects=("marketbot.bad*",))


def test_every_catalog_route_uses_a_marketbot_subject() -> None:
    assert ENGINE_ROUTES
    assert all(
        route.subject.startswith("marketbot.")
        for routes in ENGINE_ROUTES.values()
        for route in routes
    )
