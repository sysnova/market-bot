"""Scoped maintenance for retained JetStream market bars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from .subjects import validate_subscription_subject


class _StreamState(Protocol):
    subjects: dict[str, int] | None


class _StreamInfo(Protocol):
    state: _StreamState


class StreamManager(Protocol):
    async def stream_info(
        self,
        name: str,
        subjects_filter: str | None = None,
    ) -> _StreamInfo: ...

    async def purge_stream(
        self,
        name: str,
        subject: str | None = None,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class MarketBarPurgeSummary:
    stream: str
    subject: str
    messages_before: int
    messages_after: int
    applied: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "stream": self.stream,
            "subject": self.subject,
            "mode": "apply" if self.applied else "dry-run",
            "messages_before": self.messages_before,
            "messages_after": self.messages_after,
            "messages_removed": self.messages_before - self.messages_after,
        }


async def purge_retained_market_bars(
    manager: StreamManager,
    *,
    stream: str = "MARKETBOT",
    prefix: str = "marketbot",
    apply: bool = False,
) -> MarketBarPurgeSummary:
    """Preview or purge only retained versioned market-bar messages."""

    subject = f"{prefix}.v1.market.bar.>"
    validate_subscription_subject(subject)
    before = _matching_message_count(
        await manager.stream_info(stream, subjects_filter=subject)
    )
    after = before
    if apply:
        await manager.purge_stream(stream, subject=subject)
        after = _matching_message_count(
            await manager.stream_info(stream, subjects_filter=subject)
        )
    return MarketBarPurgeSummary(
        stream=stream,
        subject=subject,
        messages_before=before,
        messages_after=after,
        applied=apply,
    )


async def run_market_bar_purge(
    *,
    nats_url: str,
    stream: str = "MARKETBOT",
    prefix: str = "marketbot",
    apply: bool = False,
) -> MarketBarPurgeSummary:
    import nats
    from nats.js.manager import JetStreamManager

    client = await nats.connect(servers=[nats_url], connect_timeout=2)
    try:
        manager = JetStreamManager(client)
        return await purge_retained_market_bars(
            cast(StreamManager, manager),
            stream=stream,
            prefix=prefix,
            apply=apply,
        )
    finally:
        await client.drain()


def _matching_message_count(info: _StreamInfo) -> int:
    return sum((info.state.subjects or {}).values())
