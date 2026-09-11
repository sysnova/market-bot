from datetime import UTC, datetime
from decimal import Decimal

from app.integration.opportunity_summary_monitor import opportunity_rows, render_panel


def test_summary_includes_unconfirmed_tracks_and_short_direction() -> None:
    payload = dict(
        symbol="TEST",
        status="ARMED",
        trade_side="LONG",
        armed_at="2026-09-10T14:00:00Z",
        original_price="100",
        current_price="105",
        last_market_bar_at="2026-09-10T14:15:00Z",
    )
    rows = opportunity_rows(
        [
            payload,
            dict(payload, symbol="SHORT", trade_side="SHORT"),
            dict(payload, status="CLOSED"),
        ]
    )
    assert len(rows) == 2
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["TEST"].pnl_percent == Decimal("5")
    assert by_symbol["SHORT"].pnl_percent == Decimal("-5")
    text = render_panel(rows, now=datetime(2026, 9, 10, 15, tzinfo=UTC))
    assert "10/09/26 11:00" in text
    assert "seguimiento" in text
    assert "ARMED" in text


def test_summary_keeps_all_symbols_and_pages_without_truncating_history() -> None:
    rows = opportunity_rows(
        [
            dict(
                symbol=f"T{i:03}",
                status="ARMED",
                armed_at="2026-09-10T14:00:00Z",
                original_price="100",
                current_price="101",
            )
            for i in range(150)
        ]
    )
    assert len(rows) == 150
    text = render_panel(rows, now=datetime(2026, 9, 10, tzinfo=UTC), page=14, page_size=10)
    assert "pagina 15/15" in text
    assert rows[-1].symbol in text
