"""Stable desired-universe snapshot with an explicit consumer warmup barrier."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from ._base import NonEmptyStr, StrictFrozenModel, new_uuid7


class UniverseChanged(StrictFrozenModel):
    """Replace the desired Core universe; consumers warm additions before activation."""

    change_id: UUID = Field(default_factory=new_uuid7)
    occurred_at: datetime
    source: NonEmptyStr
    universe: Literal["core"] = "core"
    previous_symbols: tuple[str, ...]
    symbols: tuple[str, ...]
    added_symbols: tuple[str, ...]
    removed_symbols: tuple[str, ...]
    consumer_warmup_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_snapshot(self) -> UniverseChanged:
        if self.change_id.version != 7:
            raise ValueError("change_id must be UUIDv7")
        for field_name in (
            "previous_symbols",
            "symbols",
            "added_symbols",
            "removed_symbols",
        ):
            values = getattr(self, field_name)
            if values != _normalized(values):
                raise ValueError(f"{field_name} must contain unique normalized symbols")
        expected_added = tuple(
            value for value in self.symbols if value not in self.previous_symbols
        )
        expected_removed = tuple(
            value for value in self.previous_symbols if value not in self.symbols
        )
        if self.added_symbols != expected_added:
            raise ValueError("added_symbols must match the universe delta")
        if self.removed_symbols != expected_removed:
            raise ValueError("removed_symbols must match the universe delta")
        return self


def _normalized(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
