"""Canonical JSON bytes and content digests used at persistence boundaries."""

import base64
import hashlib
import json
from collections.abc import Mapping, Set
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel


def _normalize(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize(asdict(value))
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        if any(not isinstance(key, str) for key in mapping):
            msg = "canonical JSON object keys must be strings"
            raise TypeError(msg)
        return {cast("str", key): _normalize(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast("list[object] | tuple[object, ...]", value)
        return [_normalize(item) for item in sequence]
    if isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        values = cast("Set[object]", value)
        normalized = [_normalize(item) for item in values]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "canonical JSON requires timezone-aware datetimes"
            raise ValueError(msg)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value


def canonical_json(value: Any) -> bytes:  # noqa: ANN401
    """Serialize a supported value to stable, compact UTF-8 JSON bytes."""
    return json.dumps(
        _normalize(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_digest(value: Any) -> str:  # noqa: ANN401
    """Return the lowercase hexadecimal SHA-256 of canonical JSON bytes."""
    return hashlib.sha256(canonical_json(value)).hexdigest()
