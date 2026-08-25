from pathlib import Path

from app.integration.engine_assembly import MarketBotAssembly
from app.integration.order_flow_composition import order_flow_input_subjects

DEFINITION = Path(__file__).resolve().parents[3] / "configs/marketbot/7.34.0.yaml"


def test_bounded_order_flow_subscribes_only_to_exact_required_hot_subjects() -> None:
    engine = MarketBotAssembly.from_path(DEFINITION).build_order_flow()

    assert engine.tracked_symbols == ("ASTS", "ASTX", "ASTN", "NBIS", "NBIZ")
    subjects = order_flow_input_subjects(engine.tracked_symbols)

    assert len(subjects) == 20
    assert "marketbot.market.data.quote.ASTS" in subjects
    assert "marketbot.market.data.trade.NBIZ" in subjects
    assert all(not subject.endswith(">") for subject in subjects)
    assert all("AAPL" not in subject for subject in subjects)
