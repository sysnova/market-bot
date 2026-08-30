"""Regression boundary for the retired four-hour geometry engine."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SEARCH_ROOTS = (ROOT / "app", ROOT / "configs", ROOT / "scripts")
SOURCE_SUFFIXES = {".md", ".pine", ".ps1", ".py", ".sh", ".yaml", ".yml"}


def test_retired_four_hour_geometry_cannot_reenter_runtime_sources() -> None:
    retired_token = "".join(("swing", "channel", "4h"))
    offenders: list[str] = []
    current_test = Path(__file__).resolve()

    for search_root in SEARCH_ROOTS:
        for path in search_root.rglob("*"):
            if (
                not path.is_file()
                or path.suffix.lower() not in SOURCE_SUFFIXES
                or path.resolve() == current_test
            ):
                continue
            normalized = "".join(
                character
                for character in path.read_text(encoding="utf-8").lower()
                if character.isalnum()
            )
            if retired_token in normalized:
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
