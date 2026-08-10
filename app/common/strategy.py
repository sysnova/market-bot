"""Technical primitives for reading independently owned strategy artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

import yaml


@dataclass(frozen=True, slots=True)
class StrategySource:
    """Version and optional artifact selected by the composition root."""

    version: str
    artifact: Path | None

    def behavior(self) -> StrategyBehavior:
        if self.artifact is None:
            raise ValueError(f"strategy {self.version} does not declare an artifact")
        payload = yaml.safe_load(self.artifact.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("strategy artifact must be a mapping")
        behavior = cast("dict[str, object]", payload).get("behavior")
        if not isinstance(behavior, dict):
            raise ValueError("strategy artifact behavior must be a mapping")
        return StrategyBehavior(cast("dict[str, object]", behavior))


@dataclass(frozen=True, slots=True)
class StrategyBehavior:
    """Typed access without placing any business-rule names in shared code."""

    values: dict[str, object]

    def decimal(self, key: str) -> Decimal:
        try:
            value = Decimal(str(self.values[key]))
        except (InvalidOperation, KeyError, ValueError) as error:
            raise ValueError(f"strategy behavior {key} must be decimal") from error
        if not value.is_finite() or value < Decimal("0"):
            raise ValueError(
                f"strategy behavior {key} must be a non-negative finite decimal"
            )
        return value

    def positive_int(self, key: str) -> int:
        value = self.values.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"strategy behavior {key} must be a positive integer")
        return value

    def boolean(self, key: str) -> bool:
        value = self.values.get(key)
        if not isinstance(value, bool):
            raise ValueError(f"strategy behavior {key} must be boolean")
        return value

    def non_empty_unique_strings(self, key: str) -> tuple[str, ...]:
        value = self.values.get(key)
        if not isinstance(value, list) or not value:
            raise ValueError(f"strategy behavior {key} must be a non-empty list")
        items: list[str] = []
        for item in cast("list[object]", value):
            if not isinstance(item, str):
                raise ValueError(f"strategy behavior {key} must contain names")
            items.append(item)
        if len(items) != len(set(items)):
            raise ValueError(f"strategy behavior {key} must contain unique values")
        return tuple(items)
