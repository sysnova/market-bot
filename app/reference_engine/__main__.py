"""Executable package boundary for process supervisors."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.reference_engine",
        description=(
            "Reference engine process boundary; adapters are wired by the integration root."
        ),
    )
    parser.parse_args()


if __name__ == "__main__":
    main()
