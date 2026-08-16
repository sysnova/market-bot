from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)
from app.integration.news_intelligence_store import _latest_active_results

NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


def _news(symbol: str, *, as_of: datetime, expires_at: datetime) -> AnalysisResult:
    return AnalysisResult(
        engine_id="news-intelligence",
        engine_version="1.0.0",
        symbol=symbol,
        horizon=AnalysisHorizon.NEWS,
        as_of=as_of,
        verdict=AnalysisVerdict.AVOID,
        direction=PatternDirection.BEARISH,
        score=Decimal("90"),
        confidence=Decimal("0.9"),
        reasons=("news_event:regulatory",),
        metrics=(
            NamedValue(name="materiality", value="HIGH"),
            NamedValue(name="expires_at", value=expires_at),
        ),
        context_hash=f"sha256:{'a' * 64 if symbol == 'PFE' else 'b' * 64}",
    )


def test_bootstrap_keeps_latest_non_expired_news_per_symbol() -> None:
    older = _news("PFE", as_of=NOW - timedelta(hours=3), expires_at=NOW + timedelta(hours=3))
    latest = _news("PFE", as_of=NOW - timedelta(hours=1), expires_at=NOW + timedelta(hours=6))
    expired = _news("BA", as_of=NOW - timedelta(days=2), expires_at=NOW - timedelta(hours=1))
    payloads = [
        [older.model_dump(mode="json")],
        [latest.model_dump(mode="json"), expired.model_dump(mode="json")],
    ]

    restored = _latest_active_results(payloads, now=NOW)

    assert restored == (latest,)
    expiry = next(item.value for item in restored[0].metrics if item.name == "expires_at")
    assert expiry == NOW + timedelta(hours=6)
