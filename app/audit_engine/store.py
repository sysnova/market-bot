"""Crash-tolerant, idempotent NDJSON storage for audit envelopes."""

from __future__ import annotations

import errno
import json
import os
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from app.common.canonical import canonical_json
from app.contracts import EventEnvelope

from .errors import CorruptAuditLogError, WriterAlreadyActiveError

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ACTIVE_WRITERS: set[Path] = set()
_ACTIVE_WRITERS_LOCK = threading.Lock()


class AuditStream(StrEnum):
    """Stable physical streams in a run's audit trail."""

    SERVICES = "services"
    RULE_TRACES = "rule-traces"
    DECISIONS = "decisions"


@dataclass(frozen=True, slots=True)
class AuditReceipt:
    """Confirmation that an event was durably stored or already known."""

    event_id: UUID
    path: Path
    persisted: bool
    duplicate: bool


class AuditLog:
    """Own append-only audit files rooted below a runtime directory.

    Completed lines are immutable. During startup, an unterminated final line is
    treated as a crashed append and removed; malformed completed lines fail fast.
    """

    def __init__(self, runtime_root: Path | str) -> None:
        self._root = Path(runtime_root).resolve()
        self._event_paths: dict[UUID, Path] = {}
        self._claimed_paths: set[Path] = set()
        self._path_locks: dict[Path, threading.Lock] = {}
        self._closed = False
        self._rebuild_index()

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def append(
        self,
        run_id: str,
        stream: AuditStream,
        envelope: EventEnvelope,
    ) -> AuditReceipt:
        """Append one canonical envelope and fsync it before acknowledging."""

        self._require_open()
        self._validate_run_id(run_id)
        known_path = self._event_paths.get(envelope.event_id)
        if known_path is not None:
            return AuditReceipt(envelope.event_id, known_path, False, True)

        path = self._path_for(run_id, stream, envelope)
        lock = self._path_locks.setdefault(path, threading.Lock())
        with lock:
            known_path = self._event_paths.get(envelope.event_id)
            if known_path is not None:
                return AuditReceipt(envelope.event_id, known_path, False, True)
            self._claim(path)
            _ensure_directory(path.parent)
            line = canonical_json(envelope) + b"\n"
            self._durable_append(path, line)
            self._event_paths[envelope.event_id] = path
        return AuditReceipt(envelope.event_id, path, True, False)

    def replay(self, run_id: str, stream: AuditStream) -> Iterator[EventEnvelope]:
        """Replay all daily files for a run and stream in lexical date order."""

        self._require_open()
        self._validate_run_id(run_id)
        pattern = f"*/runs/{run_id}/{stream.value}.ndjson"
        for path in sorted(self._root.glob(pattern)):
            yield from self._read_completed_lines(path)

    def close(self) -> None:
        """Release this instance's in-process writer leases."""

        if self._closed:
            return
        with _ACTIVE_WRITERS_LOCK:
            _ACTIVE_WRITERS.difference_update(self._claimed_paths)
        self._claimed_paths.clear()
        self._closed = True

    def _rebuild_index(self) -> None:
        if not self._root.exists():
            return
        for path in sorted(self._root.glob("*/runs/*/*.ndjson")):
            self._recover_tail(path)
            for envelope in self._read_completed_lines(path):
                previous = self._event_paths.setdefault(envelope.event_id, path)
                if previous != path:
                    msg = f"duplicate event_id {envelope.event_id} in {path} and {previous}"
                    raise CorruptAuditLogError(msg)

    def _recover_tail(self, path: Path) -> None:
        data = path.read_bytes()
        if not data or data.endswith(b"\n"):
            return
        last_complete = data.rfind(b"\n") + 1
        descriptor = os.open(path, os.O_RDWR)
        try:
            os.ftruncate(descriptor, last_complete)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_completed_lines(path: Path) -> Iterator[EventEnvelope]:
        with path.open("rb") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.endswith(b"\n"):
                    msg = f"unterminated audit record at {path}:{line_number}"
                    raise CorruptAuditLogError(msg)
                try:
                    yield EventEnvelope.model_validate_json(line)
                except (ValidationError, ValueError, json.JSONDecodeError) as error:
                    msg = f"internal corruption at {path}:{line_number}"
                    raise CorruptAuditLogError(msg) from error

    def _claim(self, path: Path) -> None:
        if path in self._claimed_paths:
            return
        with _ACTIVE_WRITERS_LOCK:
            if path in _ACTIVE_WRITERS:
                raise WriterAlreadyActiveError(f"audit writer already active for {path}")
            _ACTIVE_WRITERS.add(path)
        self._claimed_paths.add(path)

    def _path_for(
        self,
        run_id: str,
        stream: AuditStream,
        envelope: EventEnvelope,
    ) -> Path:
        day = envelope.occurred_at.date().isoformat()
        return self._root / day / "runs" / run_id / f"{stream.value}.ndjson"

    @staticmethod
    def _durable_append(path: Path, line: bytes) -> None:
        created = not path.exists()
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            view = memoryview(line)
            while view:
                written = os.write(descriptor, view)
                if written == 0:
                    raise OSError("zero-byte write while appending audit record")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if created:
            _fsync_directory(path.parent)

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be a safe filesystem segment")

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("audit log is closed")


def _ensure_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True)
    for directory in reversed(missing):
        _fsync_directory(directory.parent)


def _fsync_directory(path: Path) -> None:
    """Persist a directory entry where the platform/filesystem supports it."""

    unsupported = {errno.EBADF, errno.EINVAL}
    if hasattr(errno, "ENOTSUP"):
        unsupported.add(errno.ENOTSUP)
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as error:
        if os.name != "nt" and error.errno not in unsupported:
            raise
    finally:
        os.close(descriptor)
