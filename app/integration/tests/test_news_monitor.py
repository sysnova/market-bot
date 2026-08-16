from datetime import UTC, datetime

from app.alpaca_market_data.rest import AlpacaNewsArticle
from app.integration.news_monitor import NewsUniverse, format_news_article


def _article(*, article_id: int, symbols: tuple[str, ...]) -> AlpacaNewsArticle:
    return AlpacaNewsArticle(
        article_id=article_id,
        headline="Pfizer reports positive clinical data",
        summary="The company published an update for investors.",
        author="Benzinga Newsdesk",
        created_at=datetime(2026, 8, 16, 13, 30, tzinfo=UTC),
        updated_at=datetime(2026, 8, 16, 13, 31, tzinfo=UTC),
        url="https://example.com/news/101",
        symbols=symbols,
        source="benzinga",
    )


def test_holding_news_is_explicit_and_colored() -> None:
    universe = NewsUniverse(
        symbols=("PFE", "MSFT"),
        portfolio_symbols=frozenset({"PFE"}),
        holding_symbols=frozenset({"PFE"}),
    )

    rendered = format_news_article(
        _article(article_id=101, symbols=("PFE", "SPY")),
        universe,
        color=True,
    )

    assert "★ TENENCIA" in rendered
    assert "PFE" in rendered
    assert "\x1b[1;33m" in rendered
    assert rendered.endswith("\x1b[0m")


def test_non_holding_news_stays_uncolored_and_identifies_portfolio() -> None:
    universe = NewsUniverse(
        symbols=("MSFT",),
        portfolio_symbols=frozenset({"MSFT"}),
        holding_symbols=frozenset(),
    )

    rendered = format_news_article(
        _article(article_id=102, symbols=("MSFT",)),
        universe,
        color=True,
    )

    assert "PORTFOLIO" in rendered
    assert "\x1b[" not in rendered
