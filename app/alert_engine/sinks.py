"""Local console and durable NDJSON alert sinks."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.common.canonical import canonical_json
from app.contracts import LocalAlert

from .confirmed import is_audible_alert
from .formatter import format_local_alert

_NEW_YORK = ZoneInfo("America/New_York")


class ConsoleAlertSink:
    def __init__(
        self,
        *,
        stream: TextIO,
        bell: bool = False,
        color: bool | None = None,
    ) -> None:
        self._stream = stream
        self._bell = bell
        self._color = _supports_color(stream) if color is None else color

    def emit(self, alert: LocalAlert) -> None:
        bell = "\a" if self._bell and is_audible_alert(alert) else ""
        self._stream.write(f"{format_local_alert(alert, color=self._color)}{bell}\n")
        self._stream.flush()


def _supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    try:
        return stream.isatty()
    except (AttributeError, OSError):
        return False


@dataclass(frozen=True, slots=True)
class AlertSinkReceipt:
    path: Path
    persisted: bool
    duplicate: bool


class NdjsonAlertSink:
    """Rotate canonical records by market date and deduplicate across restarts."""

    def __init__(self, path: Path | str) -> None:
        self._base_path = Path(path).resolve()
        self._lock = threading.Lock()
        self._keys_by_path: dict[Path, set[str]] = {}

    def emit(self, alert: LocalAlert) -> AlertSinkReceipt:
        with self._lock:
            path = self._daily_path(alert)
            keys = self._keys_by_path.get(path)
            if keys is None:
                keys = self._recover_and_index(path)
                self._keys_by_path[path] = keys
            if alert.deduplication_key in keys:
                return AlertSinkReceipt(path, False, True)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = canonical_json(alert) + b"\n"
            descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                view = memoryview(line)
                while view:
                    written = os.write(descriptor, view)
                    if written == 0:
                        raise OSError("zero-byte write while appending alert")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            keys.add(alert.deduplication_key)
            return AlertSinkReceipt(path, True, False)

    def path_for(self, created_at: datetime) -> Path:
        """Return the immutable ledger path for one alert timestamp."""

        market_date = created_at.astimezone(_NEW_YORK).date().isoformat()
        return self._base_path.with_name(
            f"{self._base_path.stem}-{market_date}{self._base_path.suffix}"
        )

    def _daily_path(self, alert: LocalAlert) -> Path:
        return self.path_for(alert.created_at)

    @staticmethod
    def _recover_and_index(path: Path) -> set[str]:
        keys: set[str] = set()
        if not path.exists():
            return keys
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            last_complete = data.rfind(b"\n") + 1
            with path.open("r+b") as target:
                target.truncate(last_complete)
                target.flush()
                os.fsync(target.fileno())
        with path.open("rb") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    alert = LocalAlert.model_validate_json(line)
                except (ValidationError, ValueError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"invalid alert record at {path}:{line_number}"
                    ) from error
                keys.add(alert.deduplication_key)
        return keys
