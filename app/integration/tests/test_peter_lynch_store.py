from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integration.peter_lynch_store import (
    PostgresPeterLynchStore,
    lynch_analysis_is_current,
    updated_metadata,
)
from app.peter_lynch_engine import (
    AnnualEps,
    LynchCategory,
    PeterLynchEngine,
    PeterLynchEvaluation,
    PeterLynchSnapshot,
)


def _evaluation(*, eligible: bool = True) -> PeterLynchEvaluation:
    price = Decimal("20") if eligible else Decimal("60")
    return PeterLynchEngine().evaluate(
        PeterLynchSnapshot(
            symbol="TEST",
            as_of=date(2026, 8, 2),
            price=price,
            price_as_of=date(2026, 7, 31),
            ttm_eps=Decimal("2"),
            prior_ttm_eps=Decimal("1.5"),
            annual_eps=(
                AnnualEps(2022, date(2022, 12, 31), Decimal("1")),
                AnnualEps(2023, date(2023, 12, 31), Decimal("1.25")),
                AnnualEps(2024, date(2024, 12, 31), Decimal("1.55")),
                AnnualEps(2025, date(2025, 12, 31), Decimal("1.9")),
            ),
            debt=Decimal("20"),
            equity=Decimal("100"),
            goodwill=Decimal("0"),
            intangibles_ex_goodwill=Decimal("0"),
            shares_outstanding=Decimal("300000000"),
            sic=7372,
            insider_open_market_purchase_count=1,
            fundamentals_as_of=date(2026, 6, 30),
            latest_insider_purchase_at=date(2026, 6, 1),
        )
    )


def test_metadata_merge_is_idempotent_and_preserves_other_indicators() -> None:
    original = {
        "indicators": ["Q", "LYNCH", "ROT", "LYNCH"],
        "indicatorDetails": {"Q": {"score": 80}, "LYNCH": {"old": True}},
        "custom": "keep",
    }

    selected = updated_metadata(original, _evaluation())
    repeated = updated_metadata(selected, _evaluation())
    rejected = updated_metadata(repeated, _evaluation(eligible=False))

    assert selected == repeated
    assert selected["indicators"] == ["Q", "ROT", "LYNCH"]
    assert selected["indicatorDetails"]["Q"] == {"score": 80}
    assert selected["indicatorDetails"]["LYNCH"]["category"] == LynchCategory.FAST_GROWER
    assert selected["indicatorDetails"]["LYNCH"]["priceAsOf"] == "2026-07-31"
    assert selected["indicatorDetails"]["LYNCH"]["fundamentalsAsOf"] == "2026-06-30"
    assert selected["indicatorDetails"]["LYNCH"]["passedCount"] == 6
    assert selected["indicatorDetails"]["LYNCH"]["requiredCount"] == 6
    insider = selected["indicatorDetails"]["LYNCH"]["criteria"][-1]
    assert insider["name"] == "insider_buying"
    assert insider["required"] is False
    assert selected["custom"] == "keep"
    assert rejected["indicators"] == ["Q", "ROT"]
    assert rejected["indicatorDetails"]["LYNCH"]["eligible"] is False


def test_current_analysis_requires_valid_window_and_exact_versions() -> None:
    metadata = updated_metadata({}, _evaluation())

    assert lynch_analysis_is_current(
        metadata,
        as_of=date(2026, 8, 2),
        ttl_days=1,
        engine_version="1.1.0",
        policy_version="peter-lynch-screen-1.1.0",
    )
    assert not lynch_analysis_is_current(
        metadata,
        as_of=date(2026, 8, 3),
        ttl_days=1,
        engine_version="1.1.0",
        policy_version="peter-lynch-screen-1.1.0",
    )
    assert not lynch_analysis_is_current(
        metadata,
        as_of=date(2026, 8, 2),
        ttl_days=1,
        engine_version="2.0.0",
        policy_version="peter-lynch-screen-1.1.0",
    )
    assert not lynch_analysis_is_current(
        metadata,
        as_of=date(2026, 8, 2),
        ttl_days=1,
        engine_version="1.1.0",
        policy_version="peter-lynch-screen-2.0.0",
    )


@pytest.mark.unit
async def test_store_locks_active_watchlist_rows_and_updates_in_one_transaction() -> None:
    result = MagicMock()
    result.all.return_value = [SimpleNamespace(id="row-1", symbol="TEST", metadata_json={})]
    connection = AsyncMock()
    connection.execute.side_effect = [result, MagicMock()]
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    database = MagicMock()
    database.begin.return_value = context

    saved = await PostgresPeterLynchStore(database).save((_evaluation(),))

    assert saved == 1
    assert connection.execute.await_count == 2
    update_params = connection.execute.await_args_list[1].args[1]
    assert update_params["row_id"] == "row-1"
    assert '"LYNCH"' in update_params["metadata"]


@pytest.mark.unit
async def test_store_returns_only_expired_or_version_mismatched_symbols() -> None:
    current = updated_metadata({}, _evaluation())
    expired = updated_metadata({}, _evaluation())
    expired["indicatorDetails"]["LYNCH"]["evaluatedAt"] = "2026-08-01"
    old_version = updated_metadata({}, _evaluation())
    old_version["indicatorDetails"]["LYNCH"]["engineVersion"] = "1.0.0"
    result = MagicMock()
    result.all.return_value = [
        SimpleNamespace(symbol="CURRENT", metadata_json=current),
        SimpleNamespace(symbol="EXPIRED", metadata_json=expired),
        SimpleNamespace(symbol="OLD", metadata_json=old_version),
        SimpleNamespace(symbol="NEW", metadata_json={}),
    ]
    connection = AsyncMock()
    connection.execute.return_value = result
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    database = MagicMock()
    database.connect.return_value = context

    worklist = await PostgresPeterLynchStore(database).load_worklist(
        as_of=date(2026, 8, 2),
        ttl_days=1,
        engine_version="1.1.0",
        policy_version="peter-lynch-screen-1.1.0",
    )

    assert worklist.total == 4
    assert worklist.symbols == ("EXPIRED", "OLD", "NEW")
    assert worklist.skipped_current == 1
