from datetime import timedelta

from app.alert_engine.tests.test_v311 import NOW, _intraday_at, _swing_with_support
from app.alert_engine.v312 import AlertEngineV312


def test_alert_no_longer_owns_short_confirmation() -> None:
    engine = AlertEngineV312(short_symbols=("ASTS",))
    engine.ingest(_swing_with_support(support="54", atr="3"), now=NOW)

    alert = engine.ingest(_intraday_at("58"), now=NOW + timedelta(minutes=1))

    assert alert is None or "short_entry_confirmed" not in alert.reasons
