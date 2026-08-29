"""TradingView export keeps calculated geometry observable across its lifecycle."""

from datetime import UTC, datetime

from app.integration.tradingview_projection import (
    TRADINGVIEW_COLUMNS,
    TradingViewAssessment,
    project_tradingview_row,
)


def test_projection_exports_every_calculated_price_layer() -> None:
    assessment = TradingViewAssessment(
        symbol="HUT",
        data_as_of=datetime(2026, 8, 28, 19, 45, tzinfo=UTC),
        swing={
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
        },
        swing_channel={
            "occurred_at": "2026-08-28T17:30:00Z",
            "maturity": "ARMED",
            "reasons": ["ascending_channel_armed"],
            "pivot_a_at": "2026-07-20T17:30:00Z",
            "pivot_a_price": "79.0000",
            "pivot_b_at": "2026-07-22T13:30:00Z",
            "pivot_b_price": "79.5000",
            "pivot_c_at": "2026-08-05T13:30:00Z",
            "pivot_c_price": "90.0000",
            "zone_low": "78.4927",
            "zone_high": "80.9173",
            "support": "79.7050",
            "middle": "91.5000",
            "resistance": "103.2950",
            "invalidation": "77.2804",
            "slope_per_bar": "0.1667",
            "width": "23.5900",
        },
        geri={
            "zone_low": "79.1000",
            "zone_high": "81.0000",
            "invalidation": "77.9000",
            "active_level_kind": "SUPPORT",
            "active_level_price": "80.0000",
            "levels": [
                {"sequence": 1, "kind": "SUPPORT", "price": "83.9400"},
                {"sequence": 2, "kind": "RESISTANCE", "price": "88.1200"},
                {"sequence": 3, "kind": "SUPPORT", "price": "76.0300"},
            ],
        },
    )

    row = project_tradingview_row(assessment)

    assert tuple(row) == TRADINGVIEW_COLUMNS
    assert row["ticker"] == "HUT"
    assert row["zonaLowManual"] == "72.3314"
    assert row["avwapPivotManual"] == "83.2767"
    assert row["extendedTarget"] == "137.8100"
    assert row["stFib618"] == "82.1900"
    assert row["stFib50"] == "87.5000"
    assert row["channelMiddle"] == "91.5000"
    assert row["channelPivotAAtMs"] == "1784568600000"
    assert row["channelPivotAPrice"] == "79.0000"
    assert row["channelObservedAtMs"] == "1787938200000"
    assert row["channelSlopePerBar"] == "0.1667"
    assert row["channelWidth"] == "23.5900"
    assert row["channelMaturity"] == "ARMED"
    assert row["channelReasons"] == "ascending_channel_armed"
    assert row["geriActiveLevelPrice"] == "80.0000"
    assert row["geriN1Kind"] == "SUPPORT"
    assert row["geriN1Price"] == "83.9400"
    assert row["geriN2Kind"] == "RESISTANCE"
    assert row["geriN2Price"] == "88.1200"
    assert row["geriN3Kind"] == "SUPPORT"
    assert row["geriN3Price"] == "76.0300"
    assert row["swingTarget"] == "121.5000"
    assert "currentMaturity" not in row
    assert "entryWatchStatus" not in row


def test_projection_uses_zero_only_when_a_geometry_was_not_calculated() -> None:
    assessment = TradingViewAssessment(
        symbol="XLI",
        data_as_of=datetime(2026, 8, 28, 20, 0, tzinfo=UTC),
        errors={"swing_trade": "no valid LONG impulse"},
    )

    row = project_tradingview_row(assessment)

    assert row["ticker"] == "XLI"
    assert row["stFib618"] == "0"
    assert row["stFib50"] == "0"
    assert row["geriActiveLevelKind"] == "SIN_DATO"
    assert row["geriN1Kind"] == "SIN_DATO"
    assert row["geriN3Price"] == "0"
    assert assessment.errors == {"swing_trade": "no valid LONG impulse"}


def test_projection_exports_a_broken_four_hour_channel_as_historical_geometry() -> None:
    assessment = TradingViewAssessment(
        symbol="XLI",
        data_as_of=datetime(2026, 8, 28, 20, 0, tzinfo=UTC),
        swing_channel={
            "occurred_at": "2026-08-28T17:30:00Z",
            "maturity": "INVALIDATED",
            "reasons": ["projected_support_invalidation_breached"],
            "current_price": "177.1300",
            "pivot_a_at": "2026-07-20T17:30:00Z",
            "pivot_a_price": "177.5900",
            "pivot_b_at": "2026-07-22T13:30:00Z",
            "pivot_b_price": "178.1400",
            "pivot_c_at": "2026-08-05T13:30:00Z",
            "pivot_c_price": "188.1900",
            "zone_low": "187.8683",
            "zone_high": "188.5783",
            "support": "188.2233",
            "middle": "191.4150",
            "resistance": "194.6067",
            "invalidation": "187.5133",
            "slope_per_bar": "0.1833",
            "width": "6.3834",
        },
    )

    row = project_tradingview_row(assessment)

    assert row["channelZoneLow"] == "187.8683"
    assert row["channelZoneHigh"] == "188.5783"
    assert row["channelSupport"] == "188.2233"
    assert row["channelMiddle"] == "191.4150"
    assert row["channelResistance"] == "194.6067"
    assert row["channelInvalidation"] == "187.5133"
    assert row["channelPivotAAtMs"] == "1784568600000"
    assert row["channelObservedAtMs"] == "1787938200000"
    assert row["channelMaturity"] == "INVALIDATED"
    assert row["channelReasons"] == "projected_support_invalidation_breached"


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
