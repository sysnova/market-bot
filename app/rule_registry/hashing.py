"""Semantic manifest hashing shared by providers and the registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.common.canonical import sha256_digest
from app.contracts import RulePackManifest


def calculate_manifest_hash(value: RulePackManifest | Mapping[str, Any]) -> str:
    """Hash the complete canonical manifest payload, excluding only the digest itself."""

    if isinstance(value, RulePackManifest):
        payload: dict[str, Any] = value.model_dump(mode="python")
    else:
        payload = dict(value)
    payload.pop("manifest_hash", None)
    return f"sha256:{sha256_digest(payload)}"
