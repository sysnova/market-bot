"""Web dashboard for filtering and reviewing MarketBot opportunities."""

from .failure_review import (
    FailureReview,
    FailureReviewError,
    OpenAIFailureReviewer,
    build_failure_dossier,
)
from .projection import build_dashboard_snapshot, checkpoint_pnl_percent

__all__ = [
    "FailureReview",
    "FailureReviewError",
    "OpenAIFailureReviewer",
    "build_dashboard_snapshot",
    "build_failure_dossier",
    "checkpoint_pnl_percent",
]
