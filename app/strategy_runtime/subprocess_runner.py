"""Hard process isolation for pure synchronous rule execution."""

from __future__ import annotations

import multiprocessing
import time
from decimal import Decimal
from multiprocessing.connection import Connection
from typing import Any, Protocol, cast

import psutil

from app.contracts import EvaluationContext, RuleResult, RuleStatus

from .ports import RuleFunction


class _ProcessPort(Protocol):
    @property
    def pid(self) -> int | None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class _ReceiverPort(Protocol):
    def poll(self, timeout: float = 0.0) -> bool: ...

    def recv(self) -> object: ...


def _worker(
    sender: Connection,
    function: RuleFunction,
    context: EvaluationContext,
    parameters: Any,  # noqa: ANN401
) -> None:
    try:
        sender.send(("started",))
        sender.send(("result", function(context, parameters)))
    except BaseException as error:
        sender.send(("exception", type(error).__name__, str(error)))
    finally:
        sender.close()


class SubprocessRuleRunner:
    """Run one rule in a spawned child and always reap it."""

    def __init__(self, timeout_seconds: float, max_rss_bytes: int | None = None) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_rss_bytes is not None and max_rss_bytes <= 0:
            raise ValueError("max_rss_bytes must be positive")
        self._timeout_seconds = timeout_seconds
        self._max_rss_bytes = max_rss_bytes

    def run(
        self,
        function: RuleFunction,
        context: EvaluationContext,
        parameters: Any,  # noqa: ANN401
        *,
        rule_id: str,
        rule_version: str,
    ) -> RuleResult:
        started = time.monotonic()
        ctx = multiprocessing.get_context("spawn")
        receiver, sender = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_worker, args=(sender, function, context, parameters))
        try:
            process.start()
            sender.close()
            startup_failure = self._wait_for_start(process, receiver)
            if startup_failure is not None:
                return self._error(context, rule_id, rule_version, startup_failure, started)
            execution_started = time.monotonic()
            failure = self._wait(process, receiver, execution_started)
            if failure is not None:
                return self._error(context, rule_id, rule_version, failure, started)
            if not receiver.poll():
                return self._error(
                    context,
                    rule_id,
                    rule_version,
                    ("WORKER_EXIT", "worker exited without output"),
                    started,
                )
            raw_message: object = receiver.recv()
            if not isinstance(raw_message, tuple) or not raw_message:
                return self._error(
                    context,
                    rule_id,
                    rule_version,
                    ("INVALID_OUTPUT", "worker returned an invalid envelope"),
                    started,
                )
            message = cast("tuple[object, ...]", raw_message)
            if message[0] == "exception":
                detail = f"{message[1]}: {message[2]}" if message[2] else str(message[1])
                return self._error(
                    context, rule_id, rule_version, ("RULE_EXCEPTION", detail), started
                )
            output = message[1] if len(message) > 1 else None
            if not isinstance(output, RuleResult):
                return self._error(
                    context,
                    rule_id,
                    rule_version,
                    ("INVALID_OUTPUT", "rule must return RuleResult"),
                    started,
                )
            if output.rule_id != rule_id or output.rule_version != rule_version:
                return self._error(
                    context,
                    rule_id,
                    rule_version,
                    ("INVALID_OUTPUT", "rule result identity does not match compiled rule"),
                    started,
                )
            return output
        except (OSError, TypeError, ValueError) as error:
            return self._error(
                context,
                rule_id,
                rule_version,
                ("WORKER_START", f"{type(error).__name__}: {error}"),
                started,
            )
        finally:
            receiver.close()
            sender.close()
            self._terminate(process)

    def _wait(
        self, process: _ProcessPort, receiver: _ReceiverPort, started: float
    ) -> tuple[str, str] | None:
        while True:
            if receiver.poll(0.005):
                return None
            if not process.is_alive():
                process.join()
                return None if receiver.poll() else ("WORKER_EXIT", "worker exited without output")
            if time.monotonic() - started >= self._timeout_seconds:
                self._terminate(process)
                return ("RULE_TIMEOUT", "rule exceeded hard execution timeout")
            if self._max_rss_bytes is not None and process.pid is not None:
                try:
                    if psutil.Process(process.pid).memory_info().rss > self._max_rss_bytes:
                        self._terminate(process)
                        return ("RSS_LIMIT", "rule exceeded resident memory limit")
                except psutil.Error:
                    pass

    @staticmethod
    def _wait_for_start(process: _ProcessPort, receiver: _ReceiverPort) -> tuple[str, str] | None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if receiver.poll(0.01):
                message = receiver.recv()
                if message == ("started",):
                    return None
                return ("WORKER_START", "worker sent output before its start handshake")
            if not process.is_alive():
                process.join()
                return ("WORKER_START", "worker exited during startup")
        return ("WORKER_START", "worker startup handshake timed out")

    @staticmethod
    def _terminate(process: _ProcessPort) -> None:
        if process.pid is None:
            return
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.25)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.25)
        else:
            process.join()

    @staticmethod
    def _error(
        context: EvaluationContext,
        rule_id: str,
        rule_version: str,
        failure: tuple[str, str],
        started: float,
    ) -> RuleResult:
        return RuleResult(
            rule_id=rule_id,
            rule_version=rule_version,
            status=RuleStatus.ERROR,
            evaluated_at=context.as_of,
            reason=failure[1],
            error_code=failure[0],
            error_message=failure[1],
            duration_ms=Decimal(str((time.monotonic() - started) * 1000)),
        )
