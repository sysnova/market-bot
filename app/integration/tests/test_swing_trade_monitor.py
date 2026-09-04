from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import NamedValue, SwingTradeAssessment, SwingTradeMaturity, new_uuid7
from app.integration.swing_trade_monitor import (
    SwingTradeDashboard,
    format_swing_trade_dashboard,
)

NOW = datetime(2026, 8, 21, 19, 45, tzinfo=UTC)


def test_dashboard_explains_observation_and_unavailable_macd() -> None:
    dashboard = SwingTradeDashboard()
    dashboard.merge(
        _assessment().model_copy(
            update={
                "metrics": (
                    NamedValue(name="recovery_quality_mode", value="OBSERVATION"),
                    NamedValue(name="recovery_quality", value="RECOVERY_WITH_MOMENTUM"),
                    NamedValue(name="macd_4h_status", value="AVAILABLE"),
                    NamedValue(name="macd_4h_direction", value="IMPROVING"),
                    NamedValue(name="macd_4h_histogram", value=Decimal("-0.12")),
                    NamedValue(name="macd_daily_status", value="INSUFFICIENT_HISTORY"),
                )
            }
        )
    )
    rendered = format_swing_trade_dashboard(dashboard, refreshed_at=NOW, color=False)
    assert "CALIDAD (OBSERVACION): Ruptura con impulso 4H" in rendered
    assert "MACD 4H: Mejora | HIST -0.12" in rendered
    assert "MACD DIARIO: Historial insuficiente" in rendered


def _assessment(
    symbol: str = "HOOD",
    *,
    maturity: SwingTradeMaturity | None = SwingTradeMaturity.ST4,
    assessed_at: datetime = NOW,
) -> SwingTradeAssessment:
    return SwingTradeAssessment(
        symbol=symbol,
        occurred_at=assessed_at - timedelta(minutes=15),
        assessed_at=assessed_at,
        engine_version="1.0.0",
        strategy_version="1.0.0",
        maturity=maturity,
        current_price=Decimal("82"),
        impulse_low=Decimal("70"),
        impulse_low_at=NOW - timedelta(days=50),
        impulse_high=Decimal("100"),
        impulse_high_at=NOW - timedelta(days=10),
        fibonacci_50=Decimal("85"),
        fibonacci_618=Decimal("81.46"),
        fibonacci_1618=Decimal("118.54"),
        zone_low=Decimal("81.46"),
        zone_high=Decimal("85"),
        support_20d=Decimal("82"),
        resistance_20d=Decimal("96"),
        support_band_low=Decimal("81.50"),
        support_band_high=Decimal("82.50"),
        invalidation=Decimal("80"),
        primary_target=Decimal("96"),
        extended_target=Decimal("118.54"),
        atr14=Decimal("2"),
        reward_risk=Decimal("7"),
        extended_reward_risk=Decimal("18.27"),
        support_confluence=True,
        spot_in_fibonacci_zone=True,
        geri_assessment_id=new_uuid7(),
        geri_zone_low=Decimal("81.75"),
        geri_zone_high=Decimal("82.25"),
        geri_confluence=True,
        eligible=True,
        reasons=("spot_inside_fibonacci_zone", "geri_support_confluence"),
        context_hash=f"sha256:{'a' * 64}",
    )


def test_dashboard_keeps_latest_assessment_per_symbol() -> None:
    dashboard = SwingTradeDashboard()
    newest = _assessment()

    assert dashboard.merge(newest) is True
    assert dashboard.merge(_assessment(assessed_at=NOW - timedelta(minutes=15))) is False

    assert dashboard.items() == (newest,)


def test_dashboard_renders_all_swing_trade_analysis_levels() -> None:
    dashboard = SwingTradeDashboard()
    dashboard.merge(_assessment())
    dashboard.merge(_assessment("AAPL", maturity=None))

    rendered = format_swing_trade_dashboard(
        dashboard,
        refreshed_at=NOW,
        color=False,
    )

    for expected in (
        "SWING TRADE — FIBONACCI WATCHLIST",
        "TOTAL 2",
        "HOOD | ST4 | ELIGIBLE SI | SPOT 82 | R:R 7",
        "IMPULSO 70",
        "FIB 61.8 81.46 | FIB 50 85 | FIB 161.8 118.54",
        "ZONA 81.46-85 | SPOT EN ZONA SI",
        "SOPORTE 20D 82 | BANDA 81.50-82.50 | CONFLUENCIA SI",
        "INVALIDA 80 | TARGET 20D 96 | TARGET EXT 118.54",
        "GERI ZONA 81.75-82.25 | CONFLUENCIA SI",
        "RAZONES spot_inside_fibonacci_zone,geri_support_confluence",
        "AAPL | SIN_ST",
        "NO EMITE ORDENES",
    ):
        assert expected in rendered
