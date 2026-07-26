from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.contracts import StrategyMode, StrategySpec, validate_primary_uniqueness
from app.rulepacks.synthetic_core.provider import get_provider

CONFIG_ROOT = Path(__file__).parents[4] / "configs" / "strategies" / "synthetic"


def _load_strategy(path: Path) -> StrategySpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return StrategySpec.model_validate_json(json.dumps(raw))


@pytest.mark.unit
def test_primary_a_and_shadow_b_are_safe_complete_strategy_specs() -> None:
    primary = _load_strategy(CONFIG_ROOT / "primary_a.yaml")
    shadow = _load_strategy(CONFIG_ROOT / "shadow_b.yaml")
    manifest = get_provider().manifest

    assert primary.mode is StrategyMode.PRIMARY
    assert shadow.mode is StrategyMode.SHADOW
    assert primary.family == shadow.family == "synthetic"
    assert primary.engine == shadow.engine == "reference_engine"
    assert primary.run_id == shadow.run_id == "synthetic-demo"
    assert primary.rule_pack_hash == shadow.rule_pack_hash == manifest.manifest_hash
    assert tuple(step.rule_version for step in primary.pipeline) == (
        "1.0.0",
        "1.0.0",
        "1.0.0",
    )
    assert tuple(step.rule_version for step in shadow.pipeline) == (
        "1.0.0",
        "1.0.0",
        "2.0.0",
    )
    assert sum((weight.weight for weight in primary.scoring.weights), Decimal()) == Decimal("1")
    assert sum((weight.weight for weight in shadow.scoring.weights), Decimal()) == Decimal("1")
    validate_primary_uniqueness((primary, shadow))


@pytest.mark.unit
def test_strategy_yaml_contains_data_only() -> None:
    for path in CONFIG_ROOT.glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "!!python" not in text
        assert "import" not in text
        assert "eval" not in text
