"""One RAM owner for immutable bars and analytical inputs across engine processes.

Views keep only references and preserve each consumer's event position. Corrections
and differently aggregated bars must never silently overwrite another engine's view.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from hashlib import sha256
from time import monotonic
from typing import Any


@dataclass
class _Payload:
    value: str
    key: str
    references: int = 0


@dataclass
class _View:
    owner: str
    capacity: int
    series: dict[tuple[str, str], list[tuple[float, str, bool]]] = field(default_factory=lambda: {})
    values: dict[str, str] = field(default_factory=lambda: {})


class TickerCache:
    """Transport-independent state; the server serializes access under one lock."""

    def __init__(self) -> None:
        self._payloads: dict[str, _Payload] = {}
        self._views: dict[str, _View] = {}
        self._owners: dict[str, float] = {}

    def touch(self, owner: str) -> None:
        self._owners[owner] = monotonic()

    def open(self, owner: str, view: str, capacity: int) -> None:
        if view in self._views or not 0 < capacity <= 100_000:
            raise ValueError("invalid or duplicate cache view")
        self.touch(owner)
        self._views[view] = _View(owner, capacity)

    def _retain(self, value: str) -> str:
        key = sha256(value.encode()).hexdigest()
        payload = self._payloads.get(key)
        if payload is None:
            payload = _Payload(value, key)
            self._payloads[key] = payload
        payload.references += 1
        return payload.key

    def _drop(self, key: str) -> None:
        payload = self._payloads[key]
        payload.references -= 1
        if payload.references == 0:
            del self._payloads[key]

    def add(self, view: str, rows: list[list[Any]]) -> None:
        target = self._views[view]
        for symbol, timeframe, timestamp, final, payload in rows:
            series = target.series.setdefault((symbol, timeframe), [])
            index = (
                len(series)
                if not series or timestamp > series[-1][0]
                else bisect_left(series, timestamp, key=lambda item: item[0])
            )
            key = self._retain(payload)
            item = (timestamp, key, final)
            if index < len(series) and series[index][0] == timestamp:
                self._drop(series[index][1])
                series[index] = item
            else:
                series.insert(index, item)
            if len(series) > target.capacity:
                self._drop(series.pop(0)[1])

    def history(
        self, view: str, symbol: str, timeframe: str, limit: int | None, final_only: bool
    ) -> list[str]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        rows = self._views[view].series.get((symbol, timeframe), ())
        selected = [key for _, key, final in rows if final or not final_only]
        if limit is not None:
            selected = selected[-limit:]
        return [self._payloads[key].value for key in selected]

    def put(self, view: str, key: str, value: str) -> None:
        values = self._views[view].values
        previous = values.get(key)
        values[key] = self._retain(value)
        if previous is not None:
            self._drop(previous)

    def get(self, view: str, key: str) -> str | None:
        payload = self._views[view].values.get(key)
        return self._payloads[payload].value if payload is not None else None

    def keys(self, view: str) -> list[str]:
        return list(self._views[view].values)

    def snapshot(self, view: str) -> dict[str, str]:
        return {
            key: self._payloads[payload].value for key, payload in self._views[view].values.items()
        }

    def remove(self, view: str, key: str) -> None:
        payload = self._views[view].values.pop(key)
        self._drop(payload)

    def retain_symbols(self, view: str, symbols: list[str]) -> None:
        allowed = set(symbols)
        target = self._views[view]
        for key in list(target.series):
            if key[0] not in allowed:
                for _, payload, _ in target.series.pop(key):
                    self._drop(payload)

    def close(self, view: str) -> None:
        target = self._views.pop(view)
        for series in target.series.values():
            for _, payload, _ in series:
                self._drop(payload)
        for payload in target.values.values():
            self._drop(payload)

    def release(self, owner: str) -> None:
        for view, target in list(self._views.items()):
            if target.owner == owner:
                self.close(view)
        self._owners.pop(owner, None)

    def expire(self, before: float) -> None:
        for owner, seen in list(self._owners.items()):
            if seen < before:
                self.release(owner)

    def stats(self) -> dict[str, int]:
        unique_bytes = sum(len(item.value.encode()) for item in self._payloads.values())
        logical_bytes = sum(
            len(item.value.encode()) * item.references for item in self._payloads.values()
        )
        return {
            "owners": len(self._owners),
            "views": len(self._views),
            "unique_payloads": len(self._payloads),
            "payload_bytes": unique_bytes,
            "logical_payload_bytes": logical_bytes,
            "avoided_payload_bytes": logical_bytes - unique_bytes,
            "references": sum(item.references for item in self._payloads.values()),
        }
