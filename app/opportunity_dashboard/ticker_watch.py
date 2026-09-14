"""Display-only projection of published ticker evidence; never evaluates trading rules."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta
from typing import Any, cast

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
        if name.endswith(("_at", "_id", "_price", "_until", "_score", "_percent", "_level")):
            continue
        negative = bool(_NEGATIVE.search(name))
        positive = bool(_POSITIVE.search(name))
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
            }
        )
    return gates


class TickerEvidenceBook:
    """One selected symbol; newest event per engine, event kind and horizon/family."""

    def __init__(self, symbol: str, *, engines: dict[str, str] | None = None) -> None:
        self.symbol = normalize_symbol(symbol)
        self.engines = engines or {}
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
        if not global_scope and str(payload.get("symbol", "")).upper() != self.symbol:
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
        )
        current = self._items.get(identity)
        order_at = observed or received_at
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
            expiries = [
                expiry
                for name, _, value in _fields(payload)
                if name in {"expires_at", "fresh_until"} and (expiry := _date(value))
            ]
            stale_at = min([*(expiries), at + timedelta(minutes=15)]) if at else None
            freshness = (
                "UNKNOWN"
                if at is None or at > now
                else ("STALE" if stale_at is not None and now >= stale_at else "FRESH")
            )
            assessments.append(
                {
                    **{key: value for key, value in item.items() if key != "order_at"},
                    "freshness": freshness,
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
            "missing_engines": sorted(set(self.engines) - present),
            "freshness_policy": (
                "Sin actualización durante 15 min: dato antiguo; no es un gate fallido."
            ),
            "coverage": (
                "Último evento disponible por motor, tipo y horizonte. No es historial completo."
            ),
            "execution_enabled": False,
        }
