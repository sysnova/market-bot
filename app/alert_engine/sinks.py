"""Local console and durable NDJSON alert sinks."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from app.common.canonical import canonical_json
from app.contracts import LocalAlert


class ConsoleAlertSink:
    def __init__(self, *, stream: TextIO, bell: bool = False) -> None:
        self._stream = stream
        self._bell = bell

    def emit(self, alert: LocalAlert) -> None:
        bell = "\a" if self._bell else ""
        self._stream.write(
            f"[{alert.severity.value}] {alert.symbol} score={alert.score} "
            f"{alert.title} - {alert.message}{bell}\n"
        )
        self._stream.flush()


@dataclass(frozen=True, slots=True)
class AlertSinkReceipt:
    path: Path
    persisted: bool
    duplicate: bool


class NdjsonAlertSink:
    """Append complete canonical records and deduplicate across restarts."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path).resolve()
        self._lock = threading.Lock()
        self._keys: set[str] = set()
        self._recover_and_index()

    def emit(self, alert: LocalAlert) -> AlertSinkReceipt:
        with self._lock:
            if alert.deduplication_key in self._keys:
                return AlertSinkReceipt(self._path, False, True)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = canonical_json(alert) + b"\n"
            descriptor = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
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
            self._keys.add(alert.deduplication_key)
            return AlertSinkReceipt(self._path, True, False)

    def _recover_and_index(self) -> None:
        if not self._path.exists():
            return
        data = self._path.read_bytes()
        if data and not data.endswith(b"\n"):
            last_complete = data.rfind(b"\n") + 1
            with self._path.open("r+b") as target:
                target.truncate(last_complete)
                target.flush()
                os.fsync(target.fileno())
        with self._path.open("rb") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    alert = LocalAlert.model_validate_json(line)
                except (ValidationError, ValueError, json.JSONDecodeError) as error:
                    raise ValueError(
                        f"invalid alert record at {self._path}:{line_number}"
                    ) from error
                self._keys.add(alert.deduplication_key)

