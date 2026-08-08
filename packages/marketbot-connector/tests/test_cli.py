from __future__ import annotations

import json

from typer.testing import CliRunner

from marketbot_connector.cli import app


def test_standalone_cli_lists_engines() -> None:
    result = CliRunner().invoke(app, ["list-engines"])

    assert result.exit_code == 0
    catalog = json.loads(result.stdout)
    assert catalog["intraday"] == ["marketbot.v1.analysis.result.INTRADAY.>"]


def test_standalone_cli_requires_a_filter() -> None:
    result = CliRunner().invoke(app, ["subscribe"])

    assert result.exit_code == 2
    assert "at least one engine" in result.output
