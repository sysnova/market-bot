"""Visible news-risk context without suppressing analytical buy signals."""

from __future__ import annotations

from datetime import datetime

from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    EntrySignal,
    EntryWatchTransition,
    LocalAlert,
    NamedValue,
)

from .confirmed import is_buy_alert
from .v35 import ACTIONABLE_KINDS, AlertEngineV35
from .v36 import AlertEngineV36, news_expiry, news_metric


class AlertEngineV37(AlertEngineV36):
    """Preserve every buy decision and paint it red when fresh news risk exists."""

    engine_version = "3.7.0"

    def ingest(self, result: AnalysisResult, *, now: datetime) -> LocalAlert | None:
        alert = super().ingest(result, now=now)
        if alert is None or alert.kind is not AlertKind.NEWS_RISK:
            return alert
        return alert.model_copy(
            update={
                "message": (
                    "material bearish news is active; buy signals remain enabled and "
                    "will be highlighted red; informational only, no order was submitted"
                ),
                "reasons": (
                    "material_bearish_news_alert_context",
                    *result.reasons,
                ),
            }
        )

    def news_blocks_entry(self, symbol: str, *, now: datetime) -> bool:
        del symbol, now
        return False

    def active_news_risk(self, symbol: str, *, now: datetime) -> AnalysisResult | None:
        result = self._latest.get(symbol.strip().upper(), {}).get(AnalysisHorizon.NEWS)
        return result if result is not None and self._blocks(result, now=now) else None

    def annotate_entry_signal(self, signal: EntrySignal, *, now: datetime) -> EntrySignal:
        news = self.active_news_risk(signal.symbol, now=now)
        if news is None:
            return signal
        return signal.model_copy(
            update={
                "horizons": tuple(dict.fromkeys((*signal.horizons, AnalysisHorizon.NEWS))),
                "reasons": tuple(
                    dict.fromkeys(
                        (
                            *signal.reasons,
                            "news_risk_active:red_alert",
                            *_news_reasons(news),
                        )
                    )
                ),
                "source_event_ids": tuple(
                    dict.fromkeys((*signal.source_event_ids, news.analysis_id))
                ),
            }
        )

    def ingest_entry_watch(
        self, transition: EntryWatchTransition, *, now: datetime
    ) -> LocalAlert:
        alert = super().ingest_entry_watch(transition, now=now)
        news = self.active_news_risk(transition.symbol, now=now)
        return _with_news_risk(alert, news) if news is not None and is_buy_alert(alert) else alert

    def _build_named_alert(
        self,
        symbol: str,
        kind: AlertKind,
        components: tuple[AnalysisResult, ...],
        fresh: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> LocalAlert | None:
        alert = AlertEngineV35._build_named_alert(
            self, symbol, kind, components, fresh, now
        )
        if alert is None or kind not in ACTIONABLE_KINDS:
            return alert
        news = self.active_news_risk(symbol, now=now)
        return _with_news_risk(alert, news) if news is not None else alert


def _with_news_risk(alert: LocalAlert, news: AnalysisResult) -> LocalAlert:
    expiry = news_expiry(news)
    embedded = tuple(dict.fromkeys((*alert.component_analyses, news)))
    return alert.model_copy(
        update={
            "message": f"{alert.message}; active material news risk: buy signal not blocked",
            "horizons": tuple(dict.fromkeys((*alert.horizons, AnalysisHorizon.NEWS))),
            "component_analysis_ids": tuple(
                dict.fromkeys((*alert.component_analysis_ids, news.analysis_id))
            ),
            "component_analyses": embedded,
            "metrics": (
                *alert.metrics,
                NamedValue(name="news_risk_active", value=True),
                NamedValue(name="news_risk_expires_at", value=expiry),
                NamedValue(name="news_materiality", value=news_metric(news, "materiality")),
                NamedValue(name="news_article_url", value=news_metric(news, "article_url")),
            ),
            "reasons": tuple(
                dict.fromkeys((*alert.reasons, "active_material_news_risk", *_news_reasons(news)))
            ),
        }
    )


def _news_reasons(news: AnalysisResult) -> tuple[str, ...]:
    return tuple(f"news:{reason}" for reason in news.reasons)
