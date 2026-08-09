from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.contracts import UniverseChanged, universe_changed_subject

NOW = datetime(2026, 8, 9, 15, tzinfo=UTC)


def test_universe_changed_requires_normalized_exact_deltas() -> None:
    changed = UniverseChanged(
        occurred_at=NOW,
        source="postgresql-local",
        previous_symbols=("AAPL", "MSFT"),
        symbols=("MSFT", "NVDA"),
        added_symbols=("NVDA",),
        removed_symbols=("AAPL",),
    )

    assert changed.change_id.version == 7
    assert changed.consumer_warmup_required is True
    assert universe_changed_subject() == "marketbot.v1.universe.changed.core"

    with pytest.raises(ValidationError, match="added_symbols"):
        UniverseChanged(
            occurred_at=NOW,
            source="postgresql-local",
            previous_symbols=("AAPL",),
            symbols=("NVDA",),
            added_symbols=(),
            removed_symbols=("AAPL",),
        )


def test_universe_changed_rejects_non_normalized_or_duplicate_symbols() -> None:
    with pytest.raises(ValidationError, match="normalized"):
        UniverseChanged(
            occurred_at=NOW,
            source="postgresql-local",
            previous_symbols=("aapl",),
            symbols=("AAPL", "AAPL"),
            added_symbols=("AAPL",),
            removed_symbols=(),
        )
