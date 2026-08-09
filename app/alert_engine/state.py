"""Serializable decision checkpoint for Alert v3 restarts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SwingContinuationCandidate(BaseModel):
    """First qualifying Intraday observation awaiting reconfirmation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    analysis_id: UUID
    observed_at: datetime
    market_session: date


class SwingContinuationSession(BaseModel):
    """Market session in which a continuation was already confirmed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market_session: date


class AlertEngineV3State(BaseModel):
    """Complete bounded state needed to continue Alert v3 deterministically."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"
    latest_analyses: tuple[dict[str, object], ...] = ()
    continuation_candidates: tuple[SwingContinuationCandidate, ...] = ()
    continuation_sessions: tuple[SwingContinuationSession, ...] = ()
