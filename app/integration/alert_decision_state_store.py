"""PostgreSQL adapter for the Alert v3 decision checkpoint."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alert_engine import AlertEngineV3State, AlertEngineV31
from app.alert_engine.state import SwingContinuationCandidate, SwingContinuationSession
from app.contracts import AnalysisResult
from app.persistence import PersistenceUnitOfWork

_ENGINE_NAME = "alert"


class PostgresAlertDecisionStateStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        implementation_version: str = AlertEngineV31.engine_version,
    ) -> None:
        self._session_factory = session_factory
        self._implementation_version = implementation_version
        self._last_saved_state: AlertEngineV3State | None = None

    async def is_ready(self) -> bool:
        async with self._session_factory() as session:
            relations = (
                "market_bot.engine_decision_states",
                "market_bot.alert_analysis_states",
                "market_bot.alert_continuation_candidates",
                "market_bot.alert_continuation_sessions",
            )
            for name in relations:
                relation = await session.scalar(
                    text("select to_regclass(:relation)"),
                    {"relation": name},
                )
                if relation is None:
                    return False
            return True

    async def load(self) -> AlertEngineV3State | None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            analyses = await unit.alert_decision_states.load_analyses(
                _ENGINE_NAME,
                self._implementation_version,
            )
            candidates = await unit.alert_decision_states.load_candidates(
                _ENGINE_NAME,
                self._implementation_version,
            )
            sessions = await unit.alert_decision_states.load_sessions(
                _ENGINE_NAME,
                self._implementation_version,
            )
            if analyses or candidates or sessions:
                state = AlertEngineV3State(
                    latest_analyses=tuple(
                        record.payload
                        for record in sorted(
                            analyses,
                            key=lambda item: (item.symbol, item.horizon),
                        )
                    ),
                    continuation_candidates=tuple(
                        SwingContinuationCandidate.model_validate(
                            record.payload,
                            strict=False,
                        )
                        for record in sorted(candidates, key=lambda item: item.symbol)
                    ),
                    continuation_sessions=tuple(
                        SwingContinuationSession(
                            symbol=record.symbol,
                            market_session=record.market_session,
                        )
                        for record in sorted(sessions, key=lambda item: item.symbol)
                    ),
                )
                self._last_saved_state = state
                return state
            record = await unit.engine_decision_states.load(
                _ENGINE_NAME,
                self._implementation_version,
            )
        if record is None:
            return None
        if (
            record.implementation_version != self._implementation_version
            or record.state_schema_version != "1.0.0"
        ):
            return None
        state = AlertEngineV3State.model_validate(record.payload, strict=False)
        # Force the first periodic checkpoint to migrate a legacy-only state.
        self._last_saved_state = AlertEngineV3State()
        return state

    async def save(self, state: AlertEngineV3State) -> None:
        self._last_saved_state = AlertEngineV3State()
        await self.save_if_changed(state)

    async def save_if_changed(self, state: AlertEngineV3State) -> bool:
        """Persist a checkpoint only when it differs from the last durable state."""

        if state == self._last_saved_state:
            return False
        await self._persist(state)
        self._last_saved_state = state
        return True

    async def _persist(self, state: AlertEngineV3State) -> None:
        previous = self._last_saved_state or AlertEngineV3State()
        previous_analyses = _analysis_payloads(previous)
        current_analyses = _analysis_payloads(state)
        analysis_values = tuple(
            {
                "symbol": key[0],
                "horizon": key[1],
                "analysis_id": AnalysisResult.model_validate(
                    payload,
                    strict=False,
                ).analysis_id,
                "payload": payload,
            }
            for key, payload in sorted(current_analyses.items())
            if payload != previous_analyses.get(key)
        )

        previous_candidates = {
            item.symbol: item for item in previous.continuation_candidates
        }
        current_candidates = {item.symbol: item for item in state.continuation_candidates}
        candidate_values = tuple(
            {
                "symbol": symbol,
                "active": candidate is not None,
                "payload": (
                    candidate.model_dump(mode="json") if candidate is not None else {}
                ),
            }
            for symbol in sorted(previous_candidates.keys() | current_candidates.keys())
            if (candidate := current_candidates.get(symbol)) != previous_candidates.get(symbol)
        )

        previous_sessions = {
            item.symbol: item.market_session for item in previous.continuation_sessions
        }
        session_values = tuple(
            {"symbol": item.symbol, "market_session": item.market_session}
            for item in state.continuation_sessions
            if item.market_session != previous_sessions.get(item.symbol)
        )

        async with PersistenceUnitOfWork(self._session_factory) as unit:
            await unit.alert_decision_states.upsert_analyses(
                engine_name=_ENGINE_NAME,
                implementation_version=self._implementation_version,
                values=analysis_values,
            )
            await unit.alert_decision_states.upsert_candidates(
                engine_name=_ENGINE_NAME,
                implementation_version=self._implementation_version,
                values=candidate_values,
            )
            await unit.alert_decision_states.upsert_sessions(
                engine_name=_ENGINE_NAME,
                implementation_version=self._implementation_version,
                values=session_values,
            )


def _analysis_payloads(state: AlertEngineV3State) -> dict[tuple[str, str], dict[str, object]]:
    output: dict[tuple[str, str], dict[str, object]] = {}
    for payload in state.latest_analyses:
        result = AnalysisResult.model_validate(payload, strict=False)
        output[(result.symbol, result.horizon.value)] = payload
    return output
