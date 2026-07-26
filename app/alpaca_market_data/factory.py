"""Composition helpers using the repository's redacted Alpaca settings."""

from __future__ import annotations

from app.common.settings import AppSettings

from .engine import AlpacaMarketDataEngine
from .normalizer import AlpacaEventNormalizer
from .ports import EventPublisher
from .rest import AlpacaRestClient
from .transports import HttpxTransport, WebsocketsConnector
from .websocket import AlpacaMarketDataStream


def build_alpaca_market_data_engine(
    settings: AppSettings,
    *,
    publisher: EventPublisher,
) -> AlpacaMarketDataEngine:
    """Build read-only Alpaca ingress; Trading API settings are intentionally unused."""

    if not settings.alpaca_configured:
        raise ValueError("Alpaca market-data credentials are not configured")
    if settings.alpaca_api_key_id is None or settings.alpaca_api_secret_key is None:
        raise ValueError("Alpaca market-data credentials are not configured")
    api_key_id = settings.alpaca_api_key_id.get_secret_value()
    api_secret_key = settings.alpaca_api_secret_key.get_secret_value()
    feed = settings.alpaca_data_feed
    return AlpacaMarketDataEngine(
        rest=AlpacaRestClient(
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            base_url=str(settings.alpaca_data_base_url),
            feed=feed,
            adjustment=settings.alpaca_adjustment,
            transport=HttpxTransport(),
        ),
        stream=AlpacaMarketDataStream(
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            base_url=str(settings.alpaca_market_data_stream_url),
            feed=feed,
            connector=WebsocketsConnector(),
        ),
        publisher=publisher,
        normalizer=AlpacaEventNormalizer(feed=feed),
    )
