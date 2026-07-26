"""Async PostgreSQL engine and session factories."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def normalize_database_url(database_url: str) -> str:
    """Select SQLAlchemy's async-capable psycopg driver explicitly."""

    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return f"postgresql+psycopg://{database_url.removeprefix(prefix)}"
    raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")


def create_database_engine(database_url: str, *, require_ssl: bool = True) -> AsyncEngine:
    """Create the deliberately small pool used by a single engine process."""

    return create_async_engine(
        normalize_database_url(database_url),
        pool_size=1,
        max_overflow=1,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"sslmode": "require"} if require_ssl else {},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create sessions that preserve loaded state after a successful commit."""

    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
