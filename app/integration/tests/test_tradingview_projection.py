"""TradingView export keeps calculated geometry observable across its lifecycle."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.integration.tradingview_projection import (
    TRADINGVIEW_COLUMNS,
    TradingViewAssessment,
    _swing_invalidation_sources,
    project_tradingview_row,
)


def test_projection_exports_every_calculated_price_layer() -> None:
    assessment = TradingViewAssessment(
        symbol="HUT",
        data_as_of=datetime(2026, 8, 28, 19, 45, tzinfo=UTC),
        swing={
            "verdict": "WATCH",
            "direction": "BULLISH",
            "score": "18.00",
            "metrics": [
                {"name": "entry_zone_low", "value": "72.3314"},
                {"name": "entry_zone_high", "value": "79.7287"},
                {"name": "structural_support", "value": "76.0300"},
                {"name": "invalidation", "value": "74.8896"},
                {"name": "resistance", "value": "112.0800"},
                {"name": "liquidity_high", "value": "116.7200"},
                {"name": "pivot_low_avwap", "value": "83.2767"},
                {"name": "breakout_avwap", "value": "88.1234"},
                {"name": "daily_sma20", "value": "91.1000"},
                {"name": "daily_sma50", "value": "84.2000"},
                {"name": "target_2r", "value": "121.5000"},
                {"name": "pivot_low_anchor_at", "value": "2026-08-19T04:00:00Z"},
                {"name": "breakout_anchor_at", "value": "2026-08-21T04:00:00Z"},
                {"name": "swing_entry_gate_passed", "value": False},
            ]
        },
        swing_trade={
            "impulse_low": "65.0000",
            "impulse_high": "110.0000",
            "impulse_low_at": "2026-04-01T04:00:00Z",
            "impulse_high_at": "2026-08-10T04:00:00Z",
            "fibonacci_618": "82.1900",
            "fibonacci_50": "87.5000",
            "fibonacci_1618": "137.8100",
            "zone_low": "82.1900",
            "zone_high": "87.5000",
            "support_20d": "76.0300",
            "resistance_20d": "112.0800",
            "invalidation": "72.0000",
            "primary_target": "112.0800",
            "extended_target": "137.8100",
            "maturity": "ST1",
            "eligible": True,
            "reasons": ["valid_impulse", "base_gate_passed"],
        },
        swing_trade_status="ENGINE_ASSESSMENT",
        geri={
            "zone_low": "79.1000",
            "zone_high": "81.0000",
            "invalidation": "77.9000",
            "active_level_kind": "SUPPORT",
            "active_level_price": "80.0000",
            "active_level_sequence": 3,
            "maturity": "ARMED",
            "trade_side": "LONG",
            "bounce_confirmed": False,
            "fast_confirmation": False,
            "four_hour_confirmation": False,
            "reasons": ["g0_level_armed", "trade_side:LONG"],
            "levels": [
                {"sequence": 1, "kind": "SUPPORT", "price": "83.9400"},
                {"sequence": 2, "kind": "RESISTANCE", "price": "88.1200"},
                {"sequence": 3, "kind": "SUPPORT", "price": "76.0300"},
            ],
        },
        swing_invalidation_sources=("SWING_DIARIO", "GERI_4H"),
        support_confirmation={
            "state": "REACTION_CONFIRMED",
            "confirmation_type": "SWEEP_RECLAIM",
            "zone_low": "74.1000",
            "zone_center": "75.0000",
            "zone_high": "75.9000",
            "invalidation": "72.8000",
            "support_score": "82.0000",
            "reaction_score": "68.0000",
            "reversal_score": "51.0000",
            "actionability_score": "63.5000",
            "zone_position": "ABOVE_ZONE",
            "support_sources": ["fib_0618", "daily_sma50"],
            "impulse_origin": "65.0000",
            "impulse_origin_at": "2026-04-01T04:00:00Z",
            "impulse_peak": "110.0000",
            "impulse_advance_percent": "69.2308",
            "reasons": ["support_reaction_confirmed"],
            "metrics": [
                {"name": "impulse_fib_0500", "value": "87.5000"},
                {"name": "impulse_fib_0618", "value": "82.1900"},
                {"name": "impulse_fib_0786", "value": "74.6300"},
            ],
        },
    )

    row = project_tradingview_row(assessment)

    assert tuple(row) == TRADINGVIEW_COLUMNS
    assert row["ticker"] == "HUT"
    assert row["schemaVersion"] == "3"
    assert row["zonaLowManual"] == "72.3314"
    assert row["avwapPivotManual"] == "83.2767"
    assert row["extendedTarget"] == "137.8100"
    assert row["stFib618"] == "82.1900"
    assert row["stFib50"] == "87.5000"
    assert row["stGeometryStatus"] == "ENGINE_ASSESSMENT"
    assert row["stMaturity"] == "ST1"
    assert row["stEligible"] == "true"
    assert row["stReasons"] == "valid_impulse|base_gate_passed"
    assert row["swingVerdict"] == "WATCH"
    assert row["swingEntryGatePassed"] == "false"
    assert row["geriActiveLevelPrice"] == "80.0000"
    assert row["geriActiveLevelSequence"] == "3"
    assert row["geriMaturity"] == "ARMED"
    assert row["geriTradeSide"] == "LONG"
    assert row["geriBounceConfirmed"] == "false"
    assert row["geriReasons"] == "g0_level_armed|trade_side:LONG"
    assert row["geriN1Kind"] == "SUPPORT"
    assert row["geriN1Price"] == "83.9400"
    assert row["geriN2Kind"] == "RESISTANCE"
    assert row["geriN2Price"] == "88.1200"
    assert row["geriN3Kind"] == "SUPPORT"
    assert row["geriN3Price"] == "76.0300"
    assert row["swingTarget"] == "121.5000"
    assert row["swingInvalidationSources"] == "SWING_DIARIO|GERI_4H"
    assert row["supportState"] == "REACTION_CONFIRMED"
    assert row["supportZoneLow"] == "74.1000"
    assert row["supportFib50"] == "87.5000"
    assert row["supportFib618"] == "82.1900"
    assert row["supportFib786"] == "74.6300"
    assert "currentMaturity" not in row
    assert "entryWatchStatus" not in row


def test_projection_uses_zero_only_when_a_geometry_was_not_calculated() -> None:
    assessment = TradingViewAssessment(
        symbol="XLI",
        data_as_of=datetime(2026, 8, 28, 20, 0, tzinfo=UTC),
        swing_trade_status="ENGINE_REJECTED",
        errors={"swing_trade": "no valid LONG impulse"},
    )

    row = project_tradingview_row(assessment)

    assert row["ticker"] == "XLI"
    assert row["stFib618"] == "0"
    assert row["stFib50"] == "0"
    assert row["stGeometryStatus"] == "ENGINE_REJECTED"
    assert row["stReasons"] == "no valid LONG impulse"
    assert row["geriActiveLevelKind"] == "SIN_DATO"
    assert row["geriN1Kind"] == "SIN_DATO"
    assert row["geriN3Price"] == "0"
    assert row["swingInvalidationSources"] == "SIN_DATO"
    assert row["supportState"] == "SIN_DATO"
    assert row["supportFib618"] == "0"
    assert assessment.errors == {"swing_trade": "no valid LONG impulse"}


def test_projection_keeps_geri_resistance_as_a_line_not_as_a_buy_zone() -> None:
    assessment = TradingViewAssessment(
        symbol="HUT",
        data_as_of=datetime(2026, 8, 28, 20, 0, tzinfo=UTC),
        geri={
            "trade_side": "SHORT",
            "zone_low": "119.0907",
            "zone_high": "121.6693",
            "invalidation": "122.9585",
            "active_level_kind": "RESISTANCE",
            "active_level_price": "120.3800",
        },
    )

    row = project_tradingview_row(assessment)

    assert row["geriZoneLow"] == "0"
    assert row["geriZoneHigh"] == "0"
    assert row["geriInvalidation"] == "0"
    assert row["geriActiveLevelKind"] == "RESISTANCE"
    assert row["geriActiveLevelPrice"] == "120.3800"


def test_support_validation_triggers_only_for_real_swing_invalidation() -> None:
    rejected_without_assessment = _swing_invalidation_sources(
        swing=None,
        swing_trade=None,
        geri={"maturity": "ARMED", "reasons": ["g0_level_armed"]},
        current_price=Decimal("79.42"),
    )
    invalidated = _swing_invalidation_sources(
        swing={
            "metrics": [
                {"name": "failed_breakout_state", "value": "STRUCTURE_INVALIDATED"}
            ]
        },
        swing_trade={"current_price": "79.42", "invalidation": "80.00"},
        geri={"maturity": "INVALIDATED"},
        current_price=Decimal("79.42"),
    )

    assert rejected_without_assessment == ()
    assert invalidated == ("SWING_DIARIO", "SWINGTRADE", "GERI_4H")


def test_tradingview_pine_resolves_engine_fields_from_the_csv_header() -> None:
    pine = Path("scripts/tradingview/marketbot_operational_viewer.pine").read_text(
        encoding="utf-8"
    )

    assert "f_num_col(cfg, csvHeader" in pine
    assert "f_text_col(cfg, csvHeader" in pine
    assert "array.size(cols) >= 44" not in pine
    assert "array.size(cols) >= 61" not in pine
    assert '"stGeometryStatus"' in pine
    assert '"geriMaturity"' in pine
    assert '"supportFib618"' in pine
    assert '"swingInvalidationSources"' in pine
    assert 'var array<string> csvHeader = str.split(SCHEMA_CSV, ",")' in pine
    assert "f_header(" not in pine
    assert "f_normalize_ticker" in pine
    assert "f_split_line" in pine
    assert 'str.contains(cleanLine, "\\t")' in pine
    assert "string targetTicker = syminfo.ticker" in pine
    assert "usarTickerManual" not in pine
    assert "tickerOverride" not in pine
    assert pine.count("editable=false") == 1
    assert 'indicator("MarketBot · Engines Alpaca v3.3"' in pine
    assert "string csvStatus = not rowFound ?" not in pine
    assert 'string csvStatus = "OK v"' in pine
    assert "if not rowFound" in pine
    assert '"CSV / TAB / ;"' in pine
    assert "ERROR: CABECERA" not in pine
    assert "REFERENCE_ONLY" not in pine


def test_tradingview_export_never_uses_non_operational_swing_trade_geometry() -> None:
    projection = Path("app/integration/tradingview_projection.py").read_text(
        encoding="utf-8"
    )

    assert ".analyze_geometry(" not in projection
    assert 'swing_trade_status = "ENGINE_REJECTED"' in projection
