from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.audit_engine import (
    AuditLog,
    AuditStream,
    CorruptAuditLogError,
    WriterAlreadyActiveError,
)
from app.contracts import EventEnvelope, new_uuid7


def envelope(*, event_type: str = "service.health") -> EventEnvelope:
    return EventEnvelope(
        event_id=new_uuid7(),
        event_type=event_type,
        occurred_at=datetime(2026, 7, 25, 14, 30, tzinfo=UTC),
        source="tests",
        payload={"status": "healthy"},
    )


@pytest.mark.unit
def test_append_uses_daily_run_layout_and_replays_after_restart(tmp_path: Path) -> None:
    event = envelope()
    with AuditLog(tmp_path) as log:
        receipt = log.append("run-001", AuditStream.SERVICES, event)

    expected = tmp_path / "2026-07-25" / "runs" / "run-001" / "services.ndjson"
    assert receipt.path == expected
    assert receipt.persisted is True
    assert receipt.duplicate is False

    with AuditLog(tmp_path) as restarted:
        replayed = list(restarted.replay("run-001", AuditStream.SERVICES))

    assert replayed == [event]


@pytest.mark.unit
def test_duplicate_event_is_acknowledged_without_second_append(tmp_path: Path) -> None:
    event = envelope()
    with AuditLog(tmp_path) as log:
        first = log.append("run-001", AuditStream.SERVICES, event)
        duplicate = log.append("run-001", AuditStream.SERVICES, event)

    assert first.persisted is True
    assert duplicate.persisted is False
    assert duplicate.duplicate is True
    assert first.path.read_bytes().count(b"\n") == 1


@pytest.mark.unit
def test_restart_truncates_only_an_incomplete_final_line(tmp_path: Path) -> None:
    event = envelope()
    with AuditLog(tmp_path) as log:
        receipt = log.append("run-001", AuditStream.SERVICES, event)

    with receipt.path.open("ab") as stream:
        stream.write(b'{"event_id":"unfinished')

    with AuditLog(tmp_path) as recovered:
        assert list(recovered.replay("run-001", AuditStream.SERVICES)) == [event]

    data = receipt.path.read_bytes()
    assert data.endswith(b"\n")
    assert b"unfinished" not in data


@pytest.mark.unit
def test_restart_rejects_corruption_before_the_final_line(tmp_path: Path) -> None:
    path = tmp_path / "2026-07-25" / "runs" / "run-001" / "services.ndjson"
    path.parent.mkdir(parents=True)
    valid = envelope().model_dump(mode="json")
    path.write_text(
        json.dumps(valid) + "\n" + "not-json\n" + json.dumps(valid) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CorruptAuditLogError, match="internal corruption"):
        AuditLog(tmp_path)


@pytest.mark.unit
def test_only_one_store_can_write_each_file(tmp_path: Path) -> None:
    first = AuditLog(tmp_path)
    second = AuditLog(tmp_path)
    try:
        first.append("run-001", AuditStream.SERVICES, envelope())
        with pytest.raises(WriterAlreadyActiveError):
            second.append("run-001", AuditStream.SERVICES, envelope())
    finally:
        first.close()
        second.close()


@pytest.mark.unit
def test_new_file_syncs_parent_directory_before_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synced: list[Path] = []
    monkeypatch.setattr("app.audit_engine.store._fsync_directory", synced.append)

    with AuditLog(tmp_path) as log:
        first = log.append("run-001", AuditStream.SERVICES, envelope())
        sync_count_after_first = len(synced)
        log.append("run-001", AuditStream.SERVICES, envelope())

    assert synced[-1] == first.path.parent
    assert len(synced) == sync_count_after_first
