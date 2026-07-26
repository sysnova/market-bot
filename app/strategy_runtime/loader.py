"""Safe loading for declarative strategy documents."""

from __future__ import annotations

import json

import yaml
from pydantic import ValidationError
from yaml.error import YAMLError

from app.contracts import StrategySpec

from .errors import StrategyLoadError


def load_strategy_yaml(document: str | bytes) -> StrategySpec:
    """Load YAML without constructors and validate the strict v1 strategy contract."""

    try:
        raw = yaml.safe_load(document)
        if not isinstance(raw, dict):
            raise StrategyLoadError("strategy document must contain a mapping")
        encoded = json.dumps(raw, allow_nan=False, separators=(",", ":"))
        return StrategySpec.model_validate_json(encoded, strict=True)
    except StrategyLoadError:
        raise
    except (YAMLError, ValidationError, TypeError, ValueError) as error:
        raise StrategyLoadError(f"invalid strategy document: {error}") from error
