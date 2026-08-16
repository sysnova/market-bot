"""Operational Alpaca-news to OpenAI to AnalysisResult composition."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import yaml

from app.alpaca_market_data import AlpacaNewsArticle, AlpacaRestClient
from app.common.canonical import sha256_digest
from app.common.logging import configure_logging, get_logger
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    AnalysisResult,
    EventEnvelope,
    analysis_result_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.news_intelligence_engine import NewsIntelligenceEngine, OpenAINewsClassifier
from app.persistence import create_database_engine

from .distributed_composition import build_rest, connect_nats, write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .news_intelligence_store import PostgresNewsIntelligenceStore
from .postgres_universe import PostgresUniverseClient


async def run_news_intelligence_process(
    *, ready_path: Path | None = None, once: bool = False
) -> dict[str, object] | None:
    """Classify unseen news and publish bounded NEWS analyses; never submit orders."""

    settings = AppSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger("news-intelligence")
    degraded = _degraded_reason(settings)
    if degraded is not None:
        details = _degraded_details(settings, degraded)
        if ready_path is not None:
            write_ready(ready_path, details)
        if once:
            return details
        await asyncio.Event().wait()
        return None

    assert settings.openai_api_key is not None
    prompt_version, prompt = _load_prompt(settings.news_intelligence_prompt_path)
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    universe_provider = PostgresUniverseClient(database)
    store = PostgresNewsIntelligenceStore(database)
    alpaca = build_rest(settings)
    classifier = OpenAINewsClassifier(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.news_intelligence_model,
        prompt=prompt,
    )
    bus = None
    try:
        bus = await connect_nats(settings)
        engine = cast(
            "NewsIntelligenceEngine",
            assembly.build(EngineSlot.NEWS_INTELLIGENCE),
        )
        bootstrapped = await store.load_latest_active(now=datetime.now(UTC))
        for result in bootstrapped:
            await _publish_analysis(bus, result)
        query_start = datetime.now(UTC) - timedelta(
            hours=settings.news_intelligence_lookback_hours
        )
        startup_details: dict[str, object] = {
            "service": "news-intelligence-v1",
            "mode": "ACTIVE",
            "marketbot_definition_version": assembly.definition.version,
            "engine_version": engine.engine_version,
            "prompt_version": prompt_version,
            "model": settings.news_intelligence_model,
            "refresh_seconds": settings.news_intelligence_refresh_seconds,
            "state": "ready-before-initial-classification",
            "bootstrapped_analyses": len(bootstrapped),
            "execution_enabled": False,
        }
        if ready_path is not None and not once:
            write_ready(ready_path, startup_details)
        while True:
            cycle_started = datetime.now(UTC)
            universe = await universe_provider.get_universe()
            articles = await _fetch_batches(
                alpaca,
                universe.symbols,
                start=query_start,
                batch_size=50,
            )
            candidates = sorted(
                articles.values(), key=lambda item: (item.updated_at, item.article_id), reverse=True
            )[: settings.news_intelligence_max_articles_per_cycle]
            classified = published = skipped = failures = 0
            for article in reversed(candidates):
                content_hash = _content_hash(article)
                try:
                    if await store.is_current(article.article_id, content_hash):
                        skipped += 1
                        continue
                    assessment = await classifier.classify(article)
                    results = engine.project(
                        article,
                        assessment,
                        model=settings.news_intelligence_model,
                        prompt_version=prompt_version,
                    )
                    assessed_at = datetime.now(UTC)
                    await store.save(
                        article_id=article.article_id,
                        content_hash=content_hash,
                        article_updated_at=article.updated_at,
                        assessed_at=assessed_at,
                        model=settings.news_intelligence_model,
                        prompt_version=prompt_version,
                        assessment=assessment,
                        analysis_results=results,
                    )
                    classified += 1
                    for result in results:
                        await _publish_analysis(bus, result)
                        published += 1
                except Exception as error:
                    failures += 1
                    await logger.aexception(
                        "news_article_classification_failed",
                        article_id=article.article_id,
                        error_type=type(error).__name__,
                    )
            details: dict[str, object] = {
                "service": "news-intelligence-v1",
                "mode": "ACTIVE",
                "marketbot_definition_version": assembly.definition.version,
                "engine_version": engine.engine_version,
                "prompt_version": prompt_version,
                "model": settings.news_intelligence_model,
                "symbols": len(universe.symbols),
                "classified": classified,
                "analyses_published": published,
                "deduplicated": skipped,
                "failures": failures,
                "refresh_seconds": settings.news_intelligence_refresh_seconds,
                "execution_enabled": False,
            }
            if once:
                return details
            query_start = cycle_started - timedelta(minutes=2)
            await asyncio.sleep(settings.news_intelligence_refresh_seconds)
    finally:
        await classifier.close()
        await alpaca.close()
        if bus is not None:
            await bus.close()
        await database.dispose()


def _degraded_reason(settings: AppSettings) -> str | None:
    if not settings.alpaca_configured:
        return "alpaca_credentials_unconfigured"
    if not settings.openai_configured:
        return "openai_api_key_unconfigured"
    if not settings.news_intelligence_prompt_path.is_file():
        return "prompt_artifact_unavailable"
    return None


def _degraded_details(settings: AppSettings, reason: str) -> dict[str, object]:
    return {
        "service": "news-intelligence-v1",
        "mode": "DEGRADED",
        "reason": reason,
        "model": settings.news_intelligence_model,
        "execution_enabled": False,
    }


def _load_prompt(path: Path) -> tuple[str, str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("news prompt artifact must be a mapping")
    values = cast("dict[str, object]", payload)
    version = values.get("rule_version")
    prompt = values.get("prompt")
    if (
        not isinstance(version, str)
        or version != "1.0.0"
        or not isinstance(prompt, str)
        or not prompt.strip()
    ):
        raise ValueError("news prompt artifact is invalid")
    return version, prompt.strip()


async def _fetch_batches(
    client: AlpacaRestClient,
    symbols: tuple[str, ...],
    *,
    start: datetime,
    batch_size: int,
) -> dict[int, AlpacaNewsArticle]:
    articles: dict[int, AlpacaNewsArticle] = {}
    for index in range(0, len(symbols), batch_size):
        batch = symbols[index : index + batch_size]
        for article in await client.fetch_news(batch, start=start):
            articles[article.article_id] = article
    return articles


def _content_hash(article: AlpacaNewsArticle) -> str:
    return sha256_digest(
        {
            "article_id": article.article_id,
            "headline": article.headline,
            "summary": article.summary,
            "symbols": article.symbols,
            "updated_at": article.updated_at,
        }
    )


async def _publish_analysis(
    bus: NatsJetStreamEventBus, result: AnalysisResult
) -> None:
    await bus.publish(
        analysis_result_subject(result.horizon, result.symbol),
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            occurred_at=result.as_of,
            source="news-intelligence-v1",
            subject=result.symbol,
            payload=result,
        ),
    )
