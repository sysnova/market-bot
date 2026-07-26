from datetime import UTC, datetime
from uuid import RFC_4122, UUID

import pytest

from app.common.clock import FrozenClock
from app.common.ids import new_id, new_uuid7


def test_uuid7_has_expected_version_variant_and_timestamp() -> None:
    instant = datetime(2026, 7, 25, 18, 30, 45, 123000, tzinfo=UTC)

    identifier = new_uuid7(clock=FrozenClock(instant), randbits=lambda _: 0)

    assert identifier.version == 7
    assert identifier.variant == RFC_4122
    assert identifier.int >> 80 == int(instant.timestamp() * 1000)


def test_typed_id_has_stable_prefix_and_uuid7_payload() -> None:
    prefix, raw_uuid = new_id("order", randbits=lambda _: 0).split("_", maxsplit=1)

    assert prefix == "order"
    assert UUID(raw_uuid).version == 7


def test_id_prefix_is_validated() -> None:
    with pytest.raises(ValueError, match="prefix"):
        new_id("Bad Prefix")
