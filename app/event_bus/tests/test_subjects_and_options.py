"""Subject grammar and subscription option invariants."""

import pytest

from app.event_bus import InvalidSubjectError, SubscriptionOptions
from app.event_bus.subjects import (
    subject_matches,
    validate_publish_subject,
    validate_subscription_subject,
)


@pytest.mark.unit
@pytest.mark.parametrize("subject", ["", " padded", "two words", "a..b", "a.*", "a.>"])
def test_invalid_publish_subjects_are_rejected(subject: str) -> None:
    with pytest.raises(InvalidSubjectError):
        validate_publish_subject(subject)


@pytest.mark.unit
@pytest.mark.parametrize("subject", ["a.>.b", "a.foo*", "a.foo>"])
def test_invalid_subscription_wildcards_are_rejected(subject: str) -> None:
    with pytest.raises(InvalidSubjectError):
        validate_subscription_subject(subject)


@pytest.mark.unit
def test_tail_wildcard_requires_at_least_one_remaining_token() -> None:
    assert subject_matches("prices.>", "prices.nasdaq.updated")
    assert not subject_matches("prices.>", "prices")
    assert not subject_matches("prices.*", "prices")
    assert not subject_matches("prices.*", "orders.updated")


@pytest.mark.unit
@pytest.mark.parametrize(
    "values",
    [
        {"durable_name": " "},
        {"max_deliver": 0},
        {"ack_wait_seconds": 0},
        {"redelivery_delay_seconds": -0.1},
    ],
)
def test_invalid_subscription_options_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SubscriptionOptions(**values)  # type: ignore[arg-type]
