from datetime import UTC, datetime
from decimal import Decimal

from app.integration.open_buy_pl_monitor import buy_rows, render_panel


def test_only_filled_open_longs_are_displayed_even_when_parent_is_armed() -> None:
    leg = dict(
        status="OPEN",
        trade_side="LONG",
        entry_price="100",
        current_price="105",
        opened_at="2026-09-10T14:00:00Z",
        horizon="SWING",
        leg_id="1",
    )
    payload = dict(
        symbol="TEST",
        status="ARMED",
        legs=[leg],
        checkpoints=[dict(status="OPEN", level="L2", reached_at=leg["opened_at"])],
        last_market_bar_at="2026-09-10T14:15:00Z",
    )
    excluded = [
        dict(leg, status="WATCHING"),
        dict(leg, status="TARGET_HIT"),
        dict(leg, trade_side="SHORT"),
        dict(leg, entry_price=None),
        dict(leg, opened_at=None),
        dict(leg, signal_family="CORE_SHORT"),
    ]
    rows = buy_rows([dict(payload, legs=[leg, *excluded]), dict(payload, status="CLOSED")])
    assert len(rows) == 1
    assert rows[0].pnl_percent == Decimal("5")
    assert rows[0].pnl_per_share == Decimal("5")
    text = render_panel(rows, now=datetime(2026, 9, 10, 14, 16, tzinfo=UTC))
    assert "10/09/26 11:00" in text
    assert "+5.00%" in text
    assert "100.0000" in text


def test_closed_leg_disappears_on_next_snapshot_and_no_entry_uses_parent_price() -> None:
    p = dict(
        symbol="TEST",
        status="OPEN",
        current_price="200",
        checkpoints=[dict(status="OPEN", level="L2", reached_at="2026-09-10T14:00:00Z")],
        legs=[
            dict(
                status="OPEN",
                entry_price="100",
                current_price="97",
                opened_at="2026-09-10T14:00:00Z",
            )
        ],
    )
    assert buy_rows([p])[0].pnl_percent == Decimal("-3")
    assert buy_rows([dict(p, legs=[])]) == []
    assert "Sin compras confirmadas abiertas" in render_panel([], now=datetime.now(UTC))


def test_closed_confirmation_is_excluded_and_same_fill_horizons_are_grouped() -> None:
    at = "2026-09-10T14:00:00Z"
    leg = dict(status="OPEN", entry_price="100", current_price="101", opened_at=at)
    cp = dict(status="OPEN", level="L2", reached_at=at)
    payload = dict(
        symbol="TEST",
        status="OPEN",
        checkpoints=[cp],
        legs=[dict(leg, horizon="SWING"), dict(leg, horizon="VOLUME_STRUCTURE")],
    )
    rows = buy_rows([payload])
    assert len(rows) == 1
    assert rows[0].horizon == "OBV+SWING"
    assert buy_rows([dict(payload, checkpoints=[dict(cp, status="CLOSED")])]) == []
    assert buy_rows([dict(payload, checkpoints=[dict(cp, level="ARMED")])]) == []
