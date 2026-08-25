#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        raise FileNotFoundError(f"Missing .env at {env_path}")

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def main() -> int:
    repo_dir = Path(__file__).resolve().parents[2]
    load_env_file(repo_dir / ".env")

    api_key = os.environ.get("MARKETBOT_ALPACA_API_KEY_ID")
    secret_key = os.environ.get("MARKETBOT_ALPACA_API_SECRET_KEY")
    if not api_key or not secret_key:
        print(
            "Missing MARKETBOT_ALPACA_API_KEY_ID or MARKETBOT_ALPACA_API_SECRET_KEY",
            file=sys.stderr,
        )
        return 1

    env = os.environ.copy()
    env["ALPACA_API_KEY"] = api_key
    env["ALPACA_SECRET_KEY"] = secret_key

    uvx = shutil.which("uvx")
    if uvx is None:
        print("uvx executable was not found on PATH", file=sys.stderr)
        return 1
    proc = subprocess.run(  # noqa: S603 - resolved executable and fixed arguments
        [uvx, "alpaca-mcp-server"], cwd=repo_dir, env=env
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
