"""Database engine configuration tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.persistence import database


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgres://runtime:secret@db.example.test:5432/postgres",
            "postgresql+psycopg://runtime:secret@db.example.test:5432/postgres",
        ),
        (
            "postgresql://runtime:secret@db.example.test:5432/postgres",
            "postgresql+psycopg://runtime:secret@db.example.test:5432/postgres",
        ),
        (
            "postgresql+psycopg://runtime:secret@db.example.test:5432/postgres",
            "postgresql+psycopg://runtime:secret@db.example.test:5432/postgres",
        ),
    ],
)
def test_normalize_database_url_uses_async_psycopg_driver(raw: str, expected: str) -> None:
    assert database.normalize_database_url(raw) == expected


@pytest.mark.unit
def test_normalize_database_url_rejects_non_postgres_urls() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        database.normalize_database_url("sqlite+aiosqlite:///marketbot.db")


@pytest.mark.unit
def test_create_database_engine_uses_small_resilient_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_create_async_engine(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)

    engine = database.create_database_engine(
        "postgresql://runtime:secret@db.example.test:5432/postgres",
        require_ssl=True,
    )

    assert engine is sentinel
    assert captured == {
        "url": "postgresql+psycopg://runtime:secret@db.example.test:5432/postgres",
        "pool_size": 1,
        "max_overflow": 1,
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "connect_args": {"sslmode": "require"},
    }


@pytest.mark.unit
def test_create_database_engine_can_disable_ssl_for_local_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_async_engine(url: str, **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)
    database.create_database_engine(
        "postgresql://runtime:secret@localhost/postgres", require_ssl=False
    )

    assert captured["connect_args"] == {}


@pytest.mark.unit
def test_session_factory_does_not_expire_objects_on_commit() -> None:
    factory = database.create_session_factory(object())  # type: ignore[arg-type]

    assert factory.kw["expire_on_commit"] is False
    assert factory.kw["autoflush"] is False
