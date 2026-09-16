from datetime import UTC, datetime, timedelta

import pytest

from app.opportunity_dashboard.ticker_watch import TickerEvidenceBook, normalize_symbol

NOW = datetime(2026, 9, 14, 14, 30, tzinfo=UTC)


def evidence(symbol: str = "NVDA", **updates: object) -> dict[str, object]:
    return {
        "symbol": symbol,
        "engine_id": "swing",
        "horizon": "SWING",
        "as_of": NOW.isoformat(),
        "engine_version": "15.0.0",
        "metrics": [
            {"name": "entry_gate_passed", "value": True},
            {"name": "structure_broken_confirmed", "value": True},
            {"name": "unknown_gate", "value": None},
            {"name": "places_orders", "value": False},
        ],
        **updates,
    }


def test_gates_keep_negative_polarity_unknown_and_complete_evidence() -> None:
    book = TickerEvidenceBook("nvda", engines={"swing": "active", "4hgeri": "active"})
    assert book.merge("analysis.result.produced", evidence(), received_at=NOW)
    result = book.snapshot(now=NOW)
    assessment = result["assessments"][0]
    gates = {item["name"]: item for item in assessment["gates"]}
    assert gates["entry_gate_passed"]["status"] == "PASS"
    assert gates["structure_broken_confirmed"]["status"] == "FAIL"
    assert gates["unknown_gate"]["status"] == "UNKNOWN"
    assert "places_orders" not in gates
    assert assessment["payload"] == evidence()
    assert "4hgeri" in result["missing_engines"]


def test_old_other_symbol_and_expired_evidence_never_turn_green() -> None:
    book = TickerEvidenceBook("NVDA")
    book.merge("analysis.result.produced", evidence(), received_at=NOW)
    assert not book.merge("analysis.result.produced", evidence("AAPL"), received_at=NOW)
    assert not book.merge(
        "analysis.result.produced",
        evidence(as_of=(NOW - timedelta(minutes=5)).isoformat()),
        received_at=NOW,
    )
    stale = book.snapshot(now=NOW + timedelta(minutes=40))["assessments"][0]
    assert stale["freshness"] == "STALE"
    assert all(gate["status"] in {"STALE", "UNKNOWN"} for gate in stale["gates"])


def test_swing_open_timestamp_remains_recent_until_next_closed_bar_is_due() -> None:
    book = TickerEvidenceBook("NVDA")
    book.merge("analysis.result.produced", evidence(), received_at=NOW + timedelta(minutes=16))
    current = book.snapshot(now=NOW + timedelta(minutes=20))["assessments"][0]
    assert current["freshness"] == "FRESH"
    assert current["as_of"] == NOW.isoformat()
    assert book.snapshot(now=NOW + timedelta(minutes=32))["assessments"][0]["freshness"] == "STALE"
    # A replay received now cannot rejuvenate a halted source.
    book.merge("analysis.result.produced", evidence(), received_at=NOW + timedelta(hours=1))
    assert book.snapshot(now=NOW + timedelta(hours=1))["assessments"][0]["freshness"] == "STALE"


def test_swing_cadence_does_not_extend_expiry_other_engines_or_unknown_dates() -> None:
    for payload, expected in (
        (evidence(expires_at=(NOW + timedelta(minutes=18)).isoformat()), "STALE"),
        (evidence(engine_id="intraday", horizon="INTRADAY"), "STALE"),
        (evidence(as_of=None), "UNKNOWN"),
        (evidence(as_of=(NOW + timedelta(hours=1)).isoformat()), "UNKNOWN"),
    ):
        book = TickerEvidenceBook("NVDA")
        book.merge("analysis.result.produced", payload, received_at=NOW)
        assert (
            book.snapshot(now=NOW + timedelta(minutes=20))["assessments"][0]["freshness"]
            == expected
        )


def test_generated_date_is_recognized_without_replacing_explicit_old_data_time() -> None:
    for event, payload in (
        ("options-gamma.assessed", {"symbol": "ASTS", "generated_at": NOW.isoformat()}),
        ("market-rotation.analyzed", {"generated_at": NOW.isoformat()}),
    ):
        book = TickerEvidenceBook("ASTS")
        book.merge(event, payload, received_at=NOW)
        card = book.snapshot(now=NOW)["assessments"][0]
        assert card["as_of"] == NOW.isoformat()
        assert card["freshness"] == "FRESH"
    book = TickerEvidenceBook("ASTS")
    old = (NOW - timedelta(days=3)).isoformat()
    book.merge(
        "4hgeri.assessed",
        {
            "symbol": "ASTS",
            "occurred_at": old,
            "assessed_at": NOW.isoformat(),
            "gates": {"short_eligible": True},
        },
        received_at=NOW,
    )
    card = book.snapshot(now=NOW)["assessments"][0]
    assert card["as_of"] == old
    assert card["evaluated_at"] == NOW.isoformat()
    assert card["evaluation_freshness"] == "FRESH"
    assert card["gates"][0]["status"] == "STALE"


def test_newer_assessment_of_same_data_cannot_be_overwritten_by_old_replay() -> None:
    book = TickerEvidenceBook("ASTS")
    newer = {
        "symbol": "ASTS",
        "occurred_at": (NOW - timedelta(days=1)).isoformat(),
        "assessed_at": NOW.isoformat(),
        "state": "NEW",
    }
    book.merge("4hgeri.assessed", newer, received_at=NOW)
    assert not book.merge(
        "4hgeri.assessed",
        {
            **newer,
            "assessed_at": (NOW - timedelta(minutes=5)).isoformat(),
            "state": "OLD",
        },
        received_at=NOW + timedelta(minutes=1),
    )


def test_leveraged_assessment_matches_underlying_or_instrument_only() -> None:
    payload = {
        "underlying_symbol": "ASTS",
        "instrument_symbol": "ASTN",
        "occurred_at": NOW.isoformat(),
        "state": "OBSERVING",
    }
    for symbol, accepted in (("ASTS", True), ("ASTN", True), ("NBIS", False), ("ASTX", False)):
        book = TickerEvidenceBook(symbol)
        assert book.merge("leveraged-thesis.assessed", payload, received_at=NOW) is accepted
        if accepted:
            assert book.snapshot(now=NOW)["assessments"][0]["payload"] == payload


def test_configuration_switch_is_not_a_failed_trading_gate() -> None:
    book = TickerEvidenceBook("NVDA")
    book.merge(
        "analysis.result.produced",
        evidence(
            metrics=[
                {"name": "short_ema20_extension_hard_gate", "value": False},
            ]
        ),
        received_at=NOW,
    )
    assert book.snapshot(now=NOW)["assessments"][0]["gates"] == []


def test_nested_analyses_and_distinct_horizons_are_retained() -> None:
    book = TickerEvidenceBook("NVDA")
    book.merge(
        "entry-setup.assessed",
        {
            "symbol": "NVDA",
            "family": "CORE",
            "assessed_at": NOW.isoformat(),
            "component_analyses": [evidence(), evidence(engine_id="intraday", horizon="INTRADAY")],
        },
        received_at=NOW,
    )
    result = book.snapshot(now=NOW)
    assert {item["engine"] for item in result["assessments"]} == {
        "entry-setup",
        "swing",
        "intraday",
    }


@pytest.mark.parametrize("symbol", ["*", "AAPL.>", "", "<script>", "A B"])
def test_invalid_tickers_are_rejected(symbol: str) -> None:
    with pytest.raises(ValueError):
        normalize_symbol(symbol)


@pytest.mark.parametrize(
    "now, assessed, expected",
    [
        ("2026-09-14T16:35:00+00:00", "2026-09-14T16:34:00+00:00", "FRESH"),
        ("2026-09-14T17:31:00+00:00", "2026-09-14T17:30:00+00:00", "FRESH"),
        ("2026-09-14T17:32:00+00:00", "2026-09-14T17:31:00+00:00", "STALE"),
        ("2026-09-14T16:35:00+00:00", "2026-09-14T16:10:00+00:00", "STALE"),
        ("2026-09-14T16:35:00+00:00", None, "UNKNOWN"),
    ],
)
def test_geri_closed_friday_bar_is_valid_until_next_rth_close(
    now: str, assessed: str | None, expected: str
) -> None:
    book = TickerEvidenceBook("ASTS")
    at = datetime.fromisoformat(now)
    book.merge(
        "4hgeri.assessed",
        {
            "symbol": "ASTS",
            "occurred_at": "2026-09-11T17:30:00+00:00",
            "assessed_at": assessed,
            "short_eligible": False,
        },
        received_at=at,
    )
    card = book.snapshot(now=at)["assessments"][0]
    assert card["freshness"] == expected
    assert card["next_bar_due_at"] == "2026-09-14T17:32:00+00:00"
    assert card["gates"][0]["status"] == ("FAIL" if expected == "FRESH" else expected)


def test_geri_closed_bar_policy_never_extends_explicit_expiry() -> None:
    at = datetime.fromisoformat("2026-09-14T16:35:00+00:00")
    book = TickerEvidenceBook("ASTS")
    book.merge(
        "4hgeri.assessed",
        {
            "symbol": "ASTS",
            "occurred_at": "2026-09-11T17:30:00+00:00",
            "assessed_at": at.isoformat(),
            "expires_at": (at - timedelta(seconds=1)).isoformat(),
            "short_eligible": True,
        },
        received_at=at,
    )
    assert book.snapshot(now=at)["assessments"][0]["freshness"] == "STALE"


@pytest.mark.parametrize(
    "bar_at, now, due, expected",
    [
        (
            "2026-09-14T13:30:00+00:00",
            "2026-09-14T18:00:00+00:00",
            "2026-09-14T20:02:00+00:00",
            "FRESH",
        ),
        (
            "2026-09-14T13:30:00+00:00",
            "2026-09-14T17:29:00+00:00",
            "2026-09-14T20:02:00+00:00",
            "UNKNOWN",
        ),
        (
            "2026-09-10T17:30:00+00:00",
            "2026-09-14T16:00:00+00:00",
            "2026-09-11T17:32:00+00:00",
            "STALE",
        ),
        # The ordinary RTH deadline follows New York through the DST weekend.
        (
            "2026-10-30T17:30:00+00:00",
            "2026-11-02T17:00:00+00:00",
            "2026-11-02T18:32:00+00:00",
            "FRESH",
        ),
    ],
)
def test_geri_window_requires_closed_latest_segment_and_handles_dst(
    bar_at: str, now: str, due: str, expected: str
) -> None:
    at = datetime.fromisoformat(now)
    book = TickerEvidenceBook("ASTS")
    book.merge(
        "4hgeri.assessed",
        {
            "symbol": "ASTS",
            "occurred_at": bar_at,
            "assessed_at": now,
            "short_eligible": True,
        },
        received_at=at,
    )
    card = book.snapshot(now=at)["assessments"][0]
    assert card["freshness"] == expected
    assert card["next_bar_due_at"] == due


@pytest.mark.parametrize(
    ("data_at", "now", "expected"),
    [
        ("2026-09-15T04:00:00Z", "2026-09-16T14:03:52Z", "FRESH"),
        ("2026-09-15T04:00:00Z", "2026-09-16T19:59:00Z", "FRESH"),
        ("2026-09-15T04:00:00Z", "2026-09-16T20:02:00Z", "STALE"),
        ("2026-09-14T04:00:00Z", "2026-09-16T14:03:52Z", "STALE"),
        ("2026-09-11T04:00:00Z", "2026-09-14T14:00:00Z", "FRESH"),
        ("2026-09-16T04:00:00Z", "2026-09-16T14:00:00Z", "UNKNOWN"),
    ],
)
def test_support_daily_reference_respects_closed_bar_interval(
    data_at: str,
    now: str,
    expected: str,
) -> None:
    observed = datetime.fromisoformat(now)
    book = TickerEvidenceBook("ASTS")
    book.merge(
        "support-confirmation.assessed",
        {
            "symbol": "ASTS",
            "data_as_of": data_at,
            "assessed_at": (observed - timedelta(hours=1)).isoformat(),
            "state": "SINGLE_SUPPORT_NEARBY",
            "higher_low": True,
        },
        received_at=observed,
    )
    card = book.snapshot(now=observed)["assessments"][0]
    assert card["freshness"] == expected
    assert card["freshness_basis"] == "closed_daily_bar"
    assert card["evaluation_freshness"] == "STALE"
    assert (
        card["gates"][0]["status"]
        == {"FRESH": "PASS", "STALE": "STALE", "UNKNOWN": "UNKNOWN"}[expected]
    )
