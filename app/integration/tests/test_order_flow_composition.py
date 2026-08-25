from pathlib import Path

from app.alpaca_market_data.normalizer import AlpacaEventNormalizer
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.order_flow_composition import order_flow_input_subjects

DEFINITION = Path(__file__).resolve().parents[3] / "configs/marketbot/7.34.0.yaml"


def test_bounded_order_flow_subscribes_only_to_exact_required_hot_subjects() -> None:
    engine = MarketBotAssembly.from_path(DEFINITION).build_order_flow()

    assert engine.tracked_symbols == ("ASTS", "ASTX", "ASTN", "NBIS", "NBIZ")
    subjects = order_flow_input_subjects(engine.tracked_symbols)

    assert len(subjects) == 20
    assert "marketbot.market.data.quote.asts" in subjects
    assert "marketbot.market.data.trade.nbiz" in subjects
    assert all(not subject.endswith(">") for subject in subjects)
    assert all("aapl" not in subject for subject in subjects)


def test_bounded_subjects_match_the_live_alpaca_publications_exactly() -> None:
    normalizer = AlpacaEventNormalizer(feed="sip")
    quote = normalizer.stream_message(
        {
            "T": "q",
            "S": "ASTS",
            "bx": "V",
            "bp": 63.83,
            "bs": 10,
            "ax": "K",
            "ap": 63.84,
            "as": 20,
            "t": "2026-08-25T13:30:00Z",
            "c": ["R"],
            "z": "C",
        }
    )
    trade = normalizer.stream_message(
        {
            "T": "t",
            "S": "ASTS",
            "i": 1234,
            "x": "V",
            "p": 63.84,
            "s": 100,
            "t": "2026-08-25T13:30:00Z",
            "c": ["@"],
            "z": "C",
        }
    )

    subjects = set(order_flow_input_subjects(("ASTS",)))

    assert f"marketbot.{quote.subject}" in subjects
    assert f"marketbot.{trade.subject}" in subjects
