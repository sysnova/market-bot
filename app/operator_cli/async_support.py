"""Async execution support shared by operator command modules."""

import asyncio
import selectors
from collections.abc import Coroutine
from typing import Any


def run_async[ResultT](coroutine: Coroutine[Any, Any, ResultT]) -> ResultT:
    """Run CLI coroutines on a loop supported by psycopg on Windows."""

    return asyncio.run(
        coroutine,
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )
