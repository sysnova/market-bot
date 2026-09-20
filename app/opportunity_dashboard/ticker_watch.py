"""Display-only projection of published ticker evidence; never evaluates trading rules."""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from .short_context import build_short_context

_NEW_YORK = ZoneInfo("America/New_York")


def _daily_window(at: datetime) -> tuple[datetime, datetime] | None:
    """Display the daily reference against ordinary weekday closes, not event age."""
    local = at.astimezone(_NEW_YORK)
    if local.weekday() >= 5 or local.time() != time(0):
        return None
    closed = local.replace(hour=16)
    next_close = closed + timedelta(days=1)
    while next_close.weekday() >= 5:
        next_close += timedelta(days=1)
    return closed.astimezone(UTC), (next_close + timedelta(minutes=2)).astimezone(UTC)


def _four_hour_window(at: datetime) -> tuple[datetime, datetime] | None:
    """Match the runtime's weekday RTH segments, not a 15m wall-clock TTL.

    This follows the runtime's ordinary-session policy; it is not an exchange
    holiday/early-close calendar. Unrecognized bar boundaries keep the fallback.
    """
    local = at.astimezone(_NEW_YORK)
    if local.weekday() >= 5:
        return None
    if local.time() == time(9, 30):
        closed = local.replace(hour=13, minute=30)
        next_close = local.replace(hour=16, minute=0)
    elif local.time() == time(13, 30):
        closed = local.replace(hour=16, minute=0)
        next_close = local + timedelta(days=1)
        while next_close.weekday() >= 5:
            next_close += timedelta(days=1)
        next_close = next_close.replace(hour=13, minute=30)
    else:
        return None
    return closed.astimezone(UTC), (next_close + timedelta(minutes=2)).astimezone(UTC)


_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_NEGATIVE = re.compile(
    r"(?:^|_)(?:broken|blocked|veto|invalidated|failed|risk|expired|warning)(?:_|$)"
)
_POSITIVE = re.compile(
    r"(?:^|_)(?:gate|passed|eligible|confirmed|confirmation|confluence|fresh|valid|ready|"
    r"aligned|reclaim|higher_high|higher_low|above_signal|retest|local_breakout)(?:_|$)"
)


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not _SYMBOL.fullmatch(normalized):
        raise ValueError("Ingresá un ticker válido (por ejemplo AAPL o BRK.B).")
    return normalized


def _date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _fields(payload: dict[str, Any], prefix: str = "") -> list[tuple[str, str, object]]:
    """Keep every scalar and nested metric with its exact evidence path."""
    result: list[tuple[str, str, object]] = []
    for name, value in payload.items():
        path = f"{prefix}.{name}" if prefix else name
        if name in {"component_analyses", "latest_analyses", "entry_analyses"}:
            continue  # These become independent assessments with their own timestamps.
        if isinstance(value, dict):
            result.extend(_fields(cast("dict[str, Any]", value), path))
        elif isinstance(value, list):
            for index, item in enumerate(cast("list[object]", value)):
                if isinstance(item, dict):
                    metric = cast("dict[str, Any]", item)
                    if isinstance(metric.get("name"), str) and "value" in metric:
                        result.append((metric["name"], f"{path}[{index}]", metric["value"]))
                        if isinstance(metric["value"], dict):
                            result.extend(
                                _fields(
                                    cast("dict[str, Any]", metric["value"]),
                                    f"{path}[{index}].value",
                                )
                            )
                    else:
                        result.extend(_fields(metric, f"{path}[{index}]"))
        else:
            result.append((name, path, value))
    return result


def project_gates(payload: dict[str, Any], *, freshness: str) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for name, path, value in _fields(payload):
        if name == "short_ema20_extension_hard_gate":
            continue  # Configuration switch, explained separately in the SHORT section.
        if name.endswith(("_at", "_id", "_price", "_until", "_score", "_percent", "_level")):
            continue
        short_long_break = (
            name == "short_thesis_broken"
            and payload.get("engine_id") == "swing"
            and payload.get("engine_version") in {"14.0.0", "15.0.0", "16.0.0"}
        )
        negative = bool(_NEGATIVE.search(name)) and not short_long_break
        positive = bool(_POSITIVE.search(name)) or short_long_break
        if not (negative or positive):
            continue
        # Numeric risk estimates and text explanations remain in the assessment, not gates.
        if not isinstance(value, bool) and value is not None and "gate" not in name:
            continue
        status = "UNKNOWN"
        if isinstance(value, bool):
            status = "PASS" if value != negative else "FAIL"
        if freshness != "FRESH" and status != "UNKNOWN":
            status = "STALE" if freshness == "STALE" else "UNKNOWN"
        gates.append(
            {
                "name": name,
                "path": path,
                "value": value,
                "status": status,
                "polarity": "negative" if negative else "positive",
                **(
                    {
                        "label": "Estructura LONG rota: condición de estructura para SHORT",
                        "thesis_scope": "SHORT",
                        "meaning": "true favorece la estructura SHORT; no significa SHORT roto "
                        "ni confirma por sí solo una entrada. Es adverso para la tesis LONG.",
                    }
                    if short_long_break
                    else {}
                ),
            }
        )
    return gates


class TickerEvidenceBook:
    """One selected symbol; newest event per engine, event kind and horizon/family."""

    def __init__(
        self,
        symbol: str,
        *,
        engines: dict[str, str] | None = None,
        engine_versions: dict[str, str] | None = None,
    ) -> None:
        self.symbol = normalize_symbol(symbol)
        self.engines = engines or {}
        self.engine_versions = engine_versions or {}
        self._items: dict[str, dict[str, Any]] = {}
        self.revision = 0

    def merge(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        received_at: datetime,
        source_engine: str | None = None,
    ) -> bool:
        # Transition contracts wrap their current entity.
        for wrapper in ("watch", "opportunity", "assessment"):
            if isinstance(payload.get(wrapper), dict):
                payload = cast("dict[str, Any]", payload[wrapper])
                break
        global_scope = event_type == "market-rotation.analyzed"
        symbols = {str(payload.get("symbol", "")).upper()}
        if event_type == "leveraged-thesis.assessed":
            symbols.update(
                str(payload.get(field, "")).upper()
                for field in (
                    "underlying_symbol",
                    "instrument_symbol",
                )
            )
        if not global_scope and self.symbol not in symbols:
            return False
        engine = str(payload.get("engine_id") or source_engine or event_type.split(".")[0])
        engine = {"entry-watch": "entry-watcher", "entry-signal": "alert"}.get(engine, engine)
        scope = str(payload.get("horizon") or payload.get("family") or "")
        identity = f"{engine}:{event_type}:{scope}"
        observed = (
            _date(payload.get("data_as_of"))
            or _date(payload.get("as_of"))
            or _date(payload.get("occurred_at"))
            or _date(payload.get("assessed_at"))
            or _date(payload.get("updated_at"))
            or _date(payload.get("created_at"))
            or _date(payload.get("generated_at"))
        )
        evaluated = (
            _date(payload.get("assessed_at"))
            or _date(payload.get("generated_at"))
            or _date(payload.get("updated_at"))
        )
        current = self._items.get(identity)
        order_at = evaluated or observed or received_at
        if current and order_at < current["order_at"]:
            return False
        if current and current["payload"] == payload:
            return False
        self._items[identity] = {
            "id": identity,
            "engine": engine,
            "event_type": event_type,
            "scope": scope,
            "global_scope": global_scope,
            "as_of": observed.isoformat() if observed else None,
            "evaluated_at": evaluated.isoformat() if evaluated else None,
            "received_at": received_at.isoformat(),
            "order_at": order_at,
            "payload": copy.deepcopy(payload),
        }
        self.revision += 1
        for field in ("component_analyses", "latest_analyses", "entry_analyses"):
            for child in payload.get(field, []):
                if isinstance(child, dict):
                    self.merge(
                        "analysis.result.produced",
                        cast("dict[str, Any]", child),
                        received_at=received_at,
                    )
        return True

    def snapshot(self, *, now: datetime) -> dict[str, Any]:
        assessments: list[dict[str, Any]] = []
        for item in sorted(self._items.values(), key=lambda item: item["id"]):
            payload = item["payload"]
            at = _date(item["as_of"])
            evaluated = _date(item["evaluated_at"])
            expiries = [
                expiry
                for name, _, value in _fields(payload)
                if name in {"expires_at", "fresh_until"} and (expiry := _date(value))
            ]
            # Swing's as_of can be the opening time of its latest completed 15m bar.
            # Allow that bar to close, the next 15m evaluation, and 2m delivery grace.
            # This is display freshness only; explicit engine expiries still prevail.
            freshness_minutes = (
                32
                if item["engine"] == "swing" and item["event_type"] == "analysis.result.produced"
                else 15
            )
            stale_at = min([*expiries, at + timedelta(minutes=freshness_minutes)]) if at else None
            structural_window = (
                _four_hour_window(at)
                if at is not None and item["event_type"] == "4hgeri.assessed"
                else None
            )
            daily_window = (
                _daily_window(at)
                if at is not None and item["event_type"] == "support-confirmation.assessed"
                else None
            )
            if daily_window is not None:
                stale_at = min([*expiries, daily_window[1]])
            if structural_window is not None:
                stale_at = min([*expiries, structural_window[1]])
                if evaluated is not None:
                    stale_at = min(stale_at, evaluated + timedelta(minutes=15))
            freshness = (
                "UNKNOWN"
                if at is None
                or at > now
                or (daily_window is not None and now < daily_window[0])
                or (
                    structural_window is not None
                    and (evaluated is None or evaluated > now or now < structural_window[0])
                )
                else ("STALE" if stale_at is not None and now >= stale_at else "FRESH")
            )
            reference_window = daily_window or structural_window
            assessments.append(
                {
                    **{key: value for key, value in item.items() if key != "order_at"},
                    "freshness": freshness,
                    "freshness_basis": (
                        "closed_daily_bar"
                        if daily_window
                        else "closed_4h_bar"
                        if structural_window
                        else "event_age"
                    ),
                    "data_session_date": (
                        at.astimezone(_NEW_YORK).date().isoformat() if daily_window and at else None
                    ),
                    "next_bar_due_at": (
                        reference_window[1].isoformat()
                        if reference_window
                        else None
                    ),
                    "evaluation_freshness": (
                        "UNKNOWN"
                        if evaluated is None or evaluated > now
                        else "FRESH"
                        if now - evaluated < timedelta(minutes=15)
                        else "STALE"
                    ),
                    "age_seconds": max(0, (now - at).total_seconds()) if at else None,
                    "gates": project_gates(payload, freshness=freshness),
                }
            )
        present = {item["engine"] for item in assessments}
        return {
            "type": "ticker_snapshot",
            "symbol": self.symbol,
            "revision": self.revision,
            "captured_at": now.isoformat(),
            "assessments": copy.deepcopy(assessments),
            "engines": self.engines,
            "engine_versions": dict(self.engine_versions),
            "short_context": build_short_context(
                assessments,
                alert_version=self.engine_versions.get("alert"),
                leveraged_thesis_version=self.engine_versions.get("leveraged-thesis"),
            ),
            "missing_engines": sorted(set(self.engines) - present),
            "freshness_policy": (
                "Antigüedad desde as_of: Swing 32 min (vela de 15 min, siguiente cierre y "
                "2 min de entrega); 4HGERI exige evaluación reciente y vela cerrada vigente "
                "hasta el próximo cierre RTH habitual + 2 min; Support Confirmation usa "
                "el cierre diario siguiente + 2 min para su referencia diaria, separado "
                "de la hora de evaluación; otros motores 15 min. "
                "Una expiración anterior prevalece. "
                "Es una política visual, no un TTL de trading."
            ),
            "coverage": (
                "Último evento disponible por motor, tipo y horizonte. No es historial completo."
            ),
            "execution_enabled": False,
        }
