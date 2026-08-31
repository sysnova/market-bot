from datetime import timedelta

import pytest
from typer.testing import CliRunner

from app.integration.opportunity_web_dashboard import OpportunityWebBook
from app.operator_cli.main import app
from app.opportunity_dashboard.tests.test_projection import NOW, opportunity


@pytest.mark.unit
def test_web_book_rejects_stale_revisions_and_retains_latest_reasons() -> None:
    book = OpportunityWebBook(history=5)
    newest = opportunity().model_copy(update={"revision": 2})
    stale = newest.model_copy(update={"revision": 1, "updated_at": NOW - timedelta(minutes=1)})

    assert book.merge(newest, reasons=("new_evidence",)) is True
    assert book.merge(stale, reasons=("stale",)) is False
    assert book.items() == (newest,)
    assert book.reasons()[str(newest.opportunity_id)] == ("new_evidence",)


@pytest.mark.unit
def test_cli_registers_opportunity_web_monitor() -> None:
    result = CliRunner().invoke(app, ["monitor", "opportunities-web", "--help"])

    assert result.exit_code == 0
    assert "real-time filterable Entry Opportunity web dashboard" in result.output
