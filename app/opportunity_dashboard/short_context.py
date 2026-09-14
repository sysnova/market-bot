"""Explain the documented SHORT route using published evidence, without deciding trades."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any


def build_short_context(
    assessments: list[dict[str, Any]], *, alert_version: str | None
) -> dict[str, Any]:
    """Keep source references and freshness; an absent alert is not a session-wide verdict."""
    route = None
    if alert_version in {"3.9.0", "3.10.0"}:
        route = {
            "decision_owner": "alert",
            "implementation": alert_version,
            "required_analysis_engines": ["swing", "intraday"],
            "requirements": [
                "Swing fresco: BEARISH, CAUTION/AVOID y short_structure_gate_passed=true.",
                "Intraday fresco: BEARISH, FAVORABLE, setup bearish_breakdown o "
                "bearish_vwap_rejection y short_mature_confirmation_gate_passed=true.",
                "Niveles publicados: invalidation_level > reference_price > objective_level; "
                "short_setup_id de Swing y short_confirmation_rule_version de Intraday.",
                "Alert debe estar habilitado y emitir short_entry_confirmed. "
                "Los gates visibles no reconstruyen su decisión ni su deduplicación.",
            ],
            "symbol_scope": (
                "La configuración de Order Flow y los subyacentes de Leveraged Thesis "
                "delimitan el universo SHORT. Este resumen no verifica esa selección."
                if alert_version == "3.10.0"
                else "Esta versión no aplica el filtro de símbolos de 3.10.0."
            ),
            "not_confirmation_gates": [
                "4hgeri",
                "leveraged-thesis.state",
                "portfolio-flow",
                "order-flow.state",
                "order-flow.quote_fresh",
            ],
        }
    return {
        "route": route,
        "structure": _evidence(assessments, "swing"),
        "timing": _evidence(assessments, "intraday"),
        "confirmation": _evidence(assessments, "alert"),
        "full_session_history": False,
        "coverage": (
            "Últimas lecturas recibidas, no la secuencia completa del día. No permiten "
            "descartar una señal anterior ni explicar toda la caída o el P/L diario. "
            "Una alerta ausente significa sin confirmación recibida en este snapshot."
        ),
    }


def _evidence(assessments: list[dict[str, Any]], engine: str) -> dict[str, Any] | None:
    candidates = [
        card
        for card in assessments
        if card["engine"] == engine
        and (
            card["event_type"] == "analysis.result.produced"
            if engine != "alert"
            else card["event_type"] == "alert.local.produced"
            and card["payload"].get("kind") == "BEARISH_CONSENSUS"
            and "short_entry_confirmed" in card["payload"].get("reasons", [])
        )
    ]
    if not candidates:
        return None
    card = max(
        candidates,
        key=lambda item: (
            datetime.fromisoformat(item["as_of"]).timestamp() if item.get("as_of") else 0
        ),
    )
    payload = card["payload"]
    fields = {
        m["name"]: m["value"]
        for m in payload.get("metrics", [])
        if str(m.get("name", "")).startswith("short_")
        or m.get("name")
        in {
            "setup",
            "reference_price",
            "invalidation_level",
            "objective_level",
            "intraday_regime",
            "confirmation_quality",
        }
    }
    return copy.deepcopy(
        {
            "assessment_id": card["id"],
            "engine": engine,
            "engine_version": payload.get("engine_version"),
            "as_of": card["as_of"],
            "freshness": card["freshness"],
            "verdict": payload.get("verdict"),
            "direction": payload.get("direction"),
            "fields": fields,
            "reasons": payload.get("reasons", []),
        }
    )
