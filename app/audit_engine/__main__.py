"""Independent audit-engine diagnostics entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

from .store import AuditLog


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify MarketBot append-only audit logs")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=Path("runtime"),
        help="root containing YYYY-MM-DD/runs audit files",
    )
    args = parser.parse_args()
    with AuditLog(args.runtime_root):
        pass
    print(f"audit logs verified: {args.runtime_root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
