from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.event_bus.stream_maintenance import purge_retained_market_bars


class _Manager:
    def __init__(self, subjects: dict[str, int]) -> None:
        self.subjects = subjects
        self.purges: list[tuple[str, str | None]] = []

    async def stream_info(
        self,
        name: str,
        subjects_filter: str | None = None,
    ) -> object:
        assert name == "MARKETBOT"
        assert subjects_filter == "marketbot.v1.market.bar.>"
        return SimpleNamespace(
            state=SimpleNamespace(
                subjects={
                    subject: count
                    for subject, count in self.subjects.items()
                    if subject.startswith("marketbot.v1.market.bar.")
                }
            )
        )

    async def purge_stream(
        self,
        name: str,
        subject: str | None = None,
    ) -> bool:
        self.purges.append((name, subject))
        self.subjects = {
            item: count
            for item, count in self.subjects.items()
            if not item.startswith("marketbot.v1.market.bar.")
        }
        return True


@pytest.mark.unit
async def test_market_bar_purge_is_preview_only_by_default() -> None:
    manager = _Manager(
        {
            "marketbot.v1.market.bar.1Min.AAPL": 10,
            "marketbot.v1.analysis.result.LONG_TERM.AAPL": 1,
        }
    )

    summary = await purge_retained_market_bars(manager, stream="MARKETBOT")

    assert summary.subject == "marketbot.v1.market.bar.>"
    assert summary.messages_before == 10
    assert summary.messages_after == 10
    assert summary.applied is False
    assert manager.purges == []


@pytest.mark.unit
async def test_market_bar_purge_applies_only_to_bar_subjects() -> None:
    manager = _Manager(
        {
            "marketbot.v1.market.bar.1Min.AAPL": 10,
            "marketbot.v1.market.bar.15Min.MSFT": 4,
            "marketbot.v1.analysis.result.LONG_TERM.AAPL": 1,
        }
    )

    summary = await purge_retained_market_bars(
        manager,
        stream="MARKETBOT",
        apply=True,
    )

    assert summary.messages_before == 14
    assert summary.messages_after == 0
    assert summary.applied is True
    assert manager.purges == [("MARKETBOT", "marketbot.v1.market.bar.>")]
    assert manager.subjects == {
        "marketbot.v1.analysis.result.LONG_TERM.AAPL": 1,
    }
