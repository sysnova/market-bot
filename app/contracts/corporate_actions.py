"""Helpers for identifying unadjusted corporate-action marks in stored entries."""

from __future__ import annotations

from decimal import Decimal

from .entry_opportunity import EntryCheckpointStatus, EntryMaturityCheckpoint
from .entry_signal import EntrySignalFamily

_SPLIT_2_FOR_1_LOW = Decimal("0.48")
_SPLIT_2_FOR_1_HIGH = Decimal("0.52")


def split_adjustment_suspected(checkpoint: EntryMaturityCheckpoint) -> bool:
    """Return true when an open long mark looks like a 2:1 split mismatch."""

    if checkpoint.status is EntryCheckpointStatus.CLOSED:
        return False
    if checkpoint.signal_family is EntrySignalFamily.CORE_SHORT:
        return False
    ratio = checkpoint.current_price / checkpoint.entry_price
    return _SPLIT_2_FOR_1_LOW <= ratio <= _SPLIT_2_FOR_1_HIGH
