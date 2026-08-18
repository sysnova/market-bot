# The generator intentionally embeds readable D3 source in one HTML f-string and
# performs its final local artifact write/render after the awaited data fetches.
# ruff: noqa: E501, ASYNC221, ASYNC240, S603

from __future__ import annotations

import argparse
import asyncio
import html as html_lib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast
from zoneinfo import ZoneInfo

import httpx

from app.common.settings import AppSettings
from app.contracts import (
    AnalysisHorizon,
    analysis_result_subject,
    geri_assessment_subject,
    options_gamma_assessment_subject,
    swing_channel_assessment_subject,
)
from app.event_bus.nats_jetstream import NatsJetStreamEventBus

NEW_YORK = ZoneInfo("America/New_York")
TARGET_POINTS = 36
DAILY_SESSIONS = 21


class _SupportsModelDump(Protocol):
    def model_dump(self, *, mode: str) -> object: ...


@dataclass(frozen=True)
class Panel:
    key: str
    title: str
    status: str
    zone_low: float | None
    zone_high: float | None
    support: float | None
    invalidation: float | None
    fill: str


def _as_mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    if hasattr(value, "model_dump"):
        dumped = cast(_SupportsModelDump, value).model_dump(mode="json")
        return _as_mapping(dumped)
    raise TypeError(f"unsupported payload type: {type(value)!r}")


def _metric(payload: Mapping[str, object], name: str) -> str | None:
    value = _metric_value(payload, name)
    return str(value) if value is not None else None


def _metric_value(payload: Mapping[str, object], name: str) -> object | None:
    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        return None
    for item in cast(list[object], metrics):
        if isinstance(item, dict) and item.get("name") == name:
            return cast(dict[str, object], item).get("value")
    return None


def _float_metric(payload: Mapping[str, object], name: str) -> float:
    raw = _metric(payload, name)
    if raw is None:
        raise KeyError(name)
    return float(raw)


def _as_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _as_text(value: object | None) -> str | None:
    return None if value is None else str(value)


def _as_string_list(value: object | None) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in cast(list[object] | tuple[object, ...], value)]


def _parse_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _divergence_indicator(payload: Mapping[str, object] | None) -> dict[str, object]:
    if payload is None:
        return {
            "availability": "SIN_DATO",
            "state": "SIN_DATO",
            "reasons": [],
        }
    return {
        "availability": "AVAILABLE",
        "state": _as_text(_metric_value(payload, "divergence_state"))
        or "NO_DIVERGENCE",
        "verdict": _as_text(payload.get("verdict")),
        "direction": _as_text(payload.get("direction")),
        "score": _as_float(payload.get("score")),
        "as_of": _as_text(payload.get("as_of")),
        "weekly_close": _as_float(_metric_value(payload, "weekly_close")),
        "price_pivot_1": _as_float(_metric_value(payload, "price_pivot_1")),
        "price_pivot_2": _as_float(_metric_value(payload, "price_pivot_2")),
        "price_pivot_1_at": _as_text(
            _metric_value(payload, "price_pivot_1_at")
        ),
        "price_pivot_2_at": _as_text(
            _metric_value(payload, "price_pivot_2_at")
        ),
        "price_change_percent": _as_float(
            _metric_value(payload, "price_change_percent")
        ),
        "obv_pivot_1": _as_float(_metric_value(payload, "obv_pivot_1")),
        "obv_pivot_2": _as_float(_metric_value(payload, "obv_pivot_2")),
        "obv_improvement_normalized": _as_float(
            _metric_value(payload, "obv_improvement_normalized")
        ),
        "pivot_separation_weeks": _as_float(
            _metric_value(payload, "pivot_separation_weeks")
        ),
        "reclaim_trigger": _as_float(
            _metric_value(payload, "reclaim_trigger")
        ),
        "invalidation": _as_float(_metric_value(payload, "invalidation")),
        "invalidation_breached_at": _as_text(
            _metric_value(payload, "invalidation_breached_at")
        ),
        "reasons": _as_string_list(payload.get("reasons")),
    }


def _gamma_indicator(
    payload: Mapping[str, object] | None, *, now_iso: str
) -> dict[str, object]:
    if payload is None:
        return {
            "availability": "SIN_DATO",
            "status": "SIN_DATO",
            "freshness": "SIN_DATO",
            "warnings": [],
        }
    now = _parse_datetime(now_iso)
    expires_at = _parse_datetime(payload.get("expires_at"))
    freshness = (
        "VIGENTE"
        if now is not None and expires_at is not None and expires_at > now
        else "VENCIDO"
    )
    return {
        "availability": "AVAILABLE",
        "freshness": freshness,
        "generated_at": _as_text(payload.get("generated_at")),
        "expires_at": _as_text(payload.get("expires_at")),
        "status": _as_text(payload.get("status")) or "UNAVAILABLE",
        "quality_score": _as_float(payload.get("quality_score")),
        "coverage_ratio": _as_float(payload.get("coverage_ratio")),
        "contract_count": _as_float(payload.get("contract_count")),
        "usable_contract_count": _as_float(payload.get("usable_contract_count")),
        "spot_price": _as_float(payload.get("spot_price")),
        "open_interest_as_of": _as_text(payload.get("open_interest_as_of")),
        "gamma_regime": _as_text(payload.get("gamma_regime")),
        "directional_bias": _as_text(payload.get("directional_bias")),
        "net_gamma_ratio": _as_float(payload.get("net_gamma_ratio")),
        "call_wall": _as_float(payload.get("call_wall")),
        "put_wall": _as_float(payload.get("put_wall")),
        "absolute_gamma_wall": _as_float(payload.get("absolute_gamma_wall")),
        "max_pain": _as_float(payload.get("max_pain")),
        "gamma_flip": _as_float(payload.get("gamma_flip")),
        "expected_move_low": _as_float(payload.get("expected_move_low")),
        "expected_move_high": _as_float(payload.get("expected_move_high")),
        "pin_risk": payload.get("pin_risk") is True,
        "acceleration_risk": payload.get("acceleration_risk") is True,
        "dealer_sign_assumption": _as_text(
            payload.get("dealer_sign_assumption")
        ),
        "warnings": _as_string_list(payload.get("warnings")),
    }


def _session_open_utc(now: datetime) -> datetime:
    local = now.astimezone(NEW_YORK)
    day = local.date()
    if local.weekday() >= 5:
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    start = datetime.combine(day, datetime.min.time(), tzinfo=NEW_YORK).replace(
        hour=9, minute=30
    )
    return start.astimezone(UTC)


async def _fetch_market_series(
    settings: AppSettings, symbol: str, start: datetime, end: datetime
) -> tuple[
    list[dict[str, object]], list[dict[str, object]], dict[str, object]
]:
    api_key = settings.alpaca_api_key_id
    secret_key = settings.alpaca_api_secret_key
    if api_key is None or secret_key is None:
        raise RuntimeError("Alpaca market-data credentials are not configured")
    headers = {
        "APCA-API-KEY-ID": api_key.get_secret_value(),
        "APCA-API-SECRET-KEY": secret_key.get_secret_value(),
    }
    minute_params = {
        "symbols": symbol,
        "timeframe": "1Min",
        "adjustment": settings.alpaca_adjustment,
        "feed": settings.alpaca_data_feed,
        "sort": "asc",
        "limit": 10000,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
    }
    daily_params = {
        **minute_params,
        "timeframe": "1Day",
        "start": (end - timedelta(days=45)).isoformat().replace("+00:00", "Z"),
        "limit": 1000,
    }
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        snapshot = await client.get(
            f"{str(settings.alpaca_data_base_url).rstrip('/')}/v2/stocks/snapshots",
            params={"symbols": symbol, "feed": settings.alpaca_data_feed},
        )
        snapshot.raise_for_status()
        snapshot_root = _as_mapping(snapshot.json())
        snapshot_payload = _as_mapping(snapshot_root[symbol])
        bars = await client.get(
            f"{str(settings.alpaca_data_base_url).rstrip('/')}/v2/stocks/bars",
            params=minute_params,
        )
        bars.raise_for_status()
        bars_root = _as_mapping(bars.json())
        bars_by_symbol = _as_mapping(bars_root["bars"])
        series_raw = bars_by_symbol[symbol]
        if not isinstance(series_raw, list):
            raise TypeError("Alpaca minute bars payload must be a list")
        series = [_as_mapping(item) for item in cast(list[object], series_raw)]
        daily_bars = await client.get(
            f"{str(settings.alpaca_data_base_url).rstrip('/')}/v2/stocks/bars",
            params=daily_params,
        )
        daily_bars.raise_for_status()
        daily_root = _as_mapping(daily_bars.json())
        daily_by_symbol = _as_mapping(daily_root.get("bars", {}))
        daily_raw = daily_by_symbol.get(symbol, [])
        if not isinstance(daily_raw, list):
            raise TypeError("Alpaca daily bars payload must be a list")
        daily_series = [_as_mapping(item) for item in cast(list[object], daily_raw)]

    # Snapshot dailyBar contains the session currently developing. Merge it with
    # historical 1Day bars by New York session date so the final candle is never
    # duplicated and an in-progress session is still visible.
    current_daily = snapshot_payload.get("dailyBar")
    combined: list[dict[str, object]] = [*daily_series]
    if isinstance(current_daily, dict) and current_daily.get("t") is not None:
        combined.append(cast(dict[str, object], current_daily))
    by_session: dict[str, dict[str, object]] = {}
    for bar in combined:
        timestamp = bar.get("t")
        if timestamp is None:
            continue
        session = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(
            NEW_YORK
        ).date().isoformat()
        by_session[session] = bar
    selected_daily = sorted(by_session.values(), key=lambda item: str(item["t"]))[
        -DAILY_SESSIONS:
    ]
    return series, selected_daily, snapshot_payload


def _sample_series(bars: list[dict[str, object]], target: int = TARGET_POINTS) -> list[dict[str, object]]:
    if len(bars) <= target:
        return [{"t": bar["t"], "v": float(str(bar["c"]))} for bar in bars]
    step = max(1, len(bars) // target)
    sampled: list[dict[str, object]] = []
    for index, bar in enumerate(bars):
        if index % step == 0:
            sampled.append({"t": bar["t"], "v": float(str(bar["c"]))})
        elif sampled:
            sampled[-1] = {"t": bar["t"], "v": float(str(bar["c"]))}
    if sampled and sampled[-1]["t"] != bars[-1]["t"]:
        sampled.append(
            {"t": bars[-1]["t"], "v": float(str(bars[-1]["c"]))}
        )
    return sampled


def _build_template(title: str, data: Mapping[str, object]) -> str:
    payload_json = json.dumps(data, separators=(",", ":"))
    return f"""<div id="engineDayPath">
  <h2>{html_lib.escape(title)}</h2>
  <div class="meta">
    <strong id="summary"></strong>
    <span id="range"></span>
    <span id="asOf"></span>
  </div>
  <div class="controls">
    <button type="button" id="playBtn">Play</button>
    <label class="slider">
      <span>avance</span>
      <input id="step" type="range" min="0" max="0" step="1" value="0" />
      <span id="stepLabel">0/0</span>
    </label>
  </div>
  <div class="panels">
    <section class="panel">
      <div class="panel-head"><strong id="panel1Title"></strong><span id="panel1Sub"></span></div>
      <div class="chart" data-panel="swing"></div>
      <div class="state" id="stateSwing"></div>
    </section>
    <section class="panel">
      <div class="panel-head"><strong id="panel2Title"></strong><span id="panel2Sub"></span></div>
      <div class="chart" data-panel="channel"></div>
      <div class="state" id="stateChannel"></div>
    </section>
    <section class="panel">
      <div class="panel-head"><strong id="panel3Title"></strong><span id="panel3Sub"></span></div>
      <div class="chart" data-panel="geri"></div>
      <div class="state" id="stateGeri"></div>
    </section>
  </div>
  <section class="panel daily-panel">
    <div class="panel-head">
      <strong>Estructura diaria · últimas 21 ruedas (~1 mes)</strong>
      <span id="dailyStructure"></span>
    </div>
    <div class="engine-legend" id="dailyLegend"></div>
    <div class="chart daily-chart" data-panel="daily"></div>
    <div class="state" id="stateDaily"></div>
  </section>
  <section class="context-panels" aria-label="Indicadores complementarios">
    <article class="indicator-card">
      <div class="panel-head">
        <strong>Divergencia semanal precio/OBV</strong>
        <span class="indicator-badge" id="divergenceBadge"></span>
      </div>
      <dl class="indicator-metrics" id="divergenceMetrics"></dl>
      <p class="indicator-note" id="divergenceNote"></p>
    </article>
    <article class="indicator-card">
      <div class="panel-head">
        <strong>Gamma de opciones</strong>
        <span class="indicator-badge" id="gammaBadge"></span>
      </div>
      <dl class="indicator-metrics" id="gammaMetrics"></dl>
      <p class="indicator-note" id="gammaWarnings"></p>
      <p class="indicator-note">Estimación con OI público bajo la convención CALL_POSITIVE_PUT_NEGATIVE; no representa posiciones reales de dealers ni reemplaza niveles Swing.</p>
    </article>
  </section>
  <div class="tooltip" id="tooltip" role="tooltip" hidden></div>
</div>

<style>
  #engineDayPath {{
    position: relative;
    width: 100%;
    color: var(--foreground);
  }}
  #engineDayPath h2 {{
    margin: 0 0 8px 0;
    font-size: 18px;
    font-weight: 500;
  }}
  #engineDayPath .meta,
  #engineDayPath .controls,
  #engineDayPath .panel-head,
  #engineDayPath .state {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
  }}
  #engineDayPath .meta {{
    margin-bottom: 10px;
    color: var(--muted-foreground);
    font-size: 12px;
  }}
  #engineDayPath .controls {{
    margin-bottom: 12px;
  }}
  #engineDayPath button {{
    appearance: none;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--foreground);
    border-radius: 10px;
    padding: 8px 12px;
    font: inherit;
    cursor: pointer;
  }}
  #engineDayPath .slider {{
    display: flex;
    gap: 10px;
    align-items: center;
    flex: 1 1 320px;
    min-width: 240px;
    font-size: 12px;
    color: var(--muted-foreground);
  }}
  #engineDayPath input[type="range"] {{
    width: 100%;
    accent-color: var(--primary);
  }}
  #engineDayPath .panels {{
    display: grid;
    gap: 16px;
  }}
  #engineDayPath .panel {{
    display: grid;
    gap: 6px;
  }}
  #engineDayPath .daily-panel {{
    margin-top: 22px;
  }}
  #engineDayPath .context-panels {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 16px;
    margin-top: 22px;
  }}
  #engineDayPath .indicator-card {{
    display: grid;
    align-content: start;
    gap: 10px;
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--card);
  }}
  #engineDayPath .indicator-badge {{
    padding: 3px 7px;
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--foreground);
  }}
  #engineDayPath .indicator-metrics {{
    display: grid;
    grid-template-columns: minmax(110px, 0.8fr) minmax(150px, 1.2fr);
    gap: 6px 12px;
    margin: 0;
    font-size: 12px;
  }}
  #engineDayPath .indicator-metrics dt {{
    color: var(--muted-foreground);
  }}
  #engineDayPath .indicator-metrics dd {{
    margin: 0;
    overflow-wrap: anywhere;
  }}
  #engineDayPath .indicator-note {{
    margin: 0;
    color: var(--muted-foreground);
    font-size: 11px;
    line-height: 1.45;
  }}
  #engineDayPath .panel-head {{
    font-size: 12px;
    color: var(--muted-foreground);
  }}
  #engineDayPath .state {{
    font-size: 12px;
    color: var(--muted-foreground);
    min-height: 16px;
  }}
  #engineDayPath .engine-legend {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px 18px;
    color: var(--muted-foreground);
    font-size: 12px;
  }}
  #engineDayPath .legend-item {{
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }}
  #engineDayPath .legend-swatch {{
    width: 18px;
    height: 3px;
    background: var(--legend-color);
  }}
  #engineDayPath .chart {{
    width: 100%;
    min-height: 220px;
  }}
  #engineDayPath .axis text,
  #engineDayPath .axis-title,
  #engineDayPath .panel-title,
  #engineDayPath .label,
  #engineDayPath .small {{
    fill: var(--foreground);
    font-size: 12px;
  }}
  #engineDayPath .small {{
    fill: var(--muted-foreground);
  }}
  #engineDayPath .axis path,
  #engineDayPath .axis line,
  #engineDayPath [data-chart-frame] {{
    stroke: var(--border);
  }}
  #engineDayPath .price-line {{
    fill: none;
    stroke: var(--viz-series-1);
    stroke-width: 2.2;
  }}
  #engineDayPath .price-point {{
    fill: var(--background);
    stroke: var(--viz-series-1);
    stroke-width: 2;
  }}
  #engineDayPath .zone-band {{
    opacity: 0.22;
  }}
  #engineDayPath .zone-edge {{
    stroke-width: 2;
  }}
  #engineDayPath .support-line {{
    stroke-width: 2;
    opacity: 0.95;
  }}
  #engineDayPath .invalidation-line {{
    stroke: var(--red);
    stroke-width: 2;
    stroke-dasharray: 4 4;
  }}
  #engineDayPath .guide-line {{
    stroke: var(--muted-foreground);
    stroke-width: 1;
    stroke-dasharray: 3 4;
  }}
  #engineDayPath .daily-zone {{
    opacity: 0.24;
  }}
  #engineDayPath .daily-zone-edge {{
    stroke-width: 1.5;
    opacity: 0.9;
  }}
  #engineDayPath .daily-zone-label {{
    fill: var(--foreground);
    font-size: 11px;
    font-weight: 500;
    paint-order: stroke;
    stroke: var(--background);
    stroke-width: 3px;
  }}
  #engineDayPath .daily-support {{
    stroke-width: 2;
  }}
  #engineDayPath .daily-wick {{
    stroke-width: 1.3;
  }}
  #engineDayPath .daily-body {{
    stroke-width: 1;
  }}
  #engineDayPath .pivot-label {{
    fill: var(--foreground);
    font-size: 11px;
    font-weight: 500;
  }}
  #engineDayPath .tooltip {{
    position: absolute;
    pointer-events: none;
    background: var(--popover);
    color: var(--popover-foreground);
    border: 1px solid var(--border);
    padding: 8px 10px;
    max-width: 280px;
    z-index: 4;
    font-size: 12px;
  }}
</style>

<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script>
(() => {{
  const data = {payload_json};
  const root = document.getElementById('engineDayPath');
  const tooltip = document.getElementById('tooltip');
  const playBtn = document.getElementById('playBtn');
  const step = document.getElementById('step');
  const stepLabel = document.getElementById('stepLabel');
  const summary = document.getElementById('summary');
  const range = document.getElementById('range');
  const asOf = document.getElementById('asOf');
  const stateSwing = document.getElementById('stateSwing');
  const stateChannel = document.getElementById('stateChannel');
  const stateGeri = document.getElementById('stateGeri');
  const stateDaily = document.getElementById('stateDaily');
  const dailyStructure = document.getElementById('dailyStructure');
  const dailyLegend = document.getElementById('dailyLegend');
  const divergenceBadge = document.getElementById('divergenceBadge');
  const divergenceMetrics = document.getElementById('divergenceMetrics');
  const divergenceNote = document.getElementById('divergenceNote');
  const gammaBadge = document.getElementById('gammaBadge');
  const gammaMetrics = document.getElementById('gammaMetrics');
  const gammaWarnings = document.getElementById('gammaWarnings');
  const panels = [
    {{
      key: 'swing',
      title: 'Swing diario',
      subtitle: data.swing.status,
      zoneLow: data.swing.zone_low,
      zoneHigh: data.swing.zone_high,
      support: data.swing.support,
      invalidation: data.swing.invalidation,
      fill: 'var(--viz-series-2)',
      stateTarget: stateSwing,
    }},
    {{
      key: 'channel',
      title: 'Swing Channel 4H',
      subtitle: data.channel.status,
      zoneLow: data.channel.zone_low,
      zoneHigh: data.channel.zone_high,
      support: data.channel.support,
      invalidation: data.channel.invalidation,
      fill: 'var(--viz-series-4)',
      stateTarget: stateChannel,
    }},
    {{
      key: 'geri',
      title: '4HGERI',
      subtitle: data.geri.status,
      zoneLow: data.geri.zone_low,
      zoneHigh: data.geri.zone_high,
      support: data.geri.support,
      invalidation: data.geri.invalidation,
      fill: 'var(--viz-series-3)',
      stateTarget: stateGeri,
    }},
  ];
  function panelSubtitle(panelData) {{
    if (panelData.zone_low != null && panelData.zone_high != null) {{
      return `${{panelData.status}} · zone ${{panelData.zone_low.toFixed(4)}}-${{panelData.zone_high.toFixed(4)}}`;
    }}
    return panelData.status === 'SIN_EMISION'
      ? 'SIN_EMISION'
      : `${{panelData.status}} · sin zona long`;
  }}
  document.getElementById('panel1Title').textContent = 'Swing diario';
  document.getElementById('panel1Sub').textContent = panelSubtitle(data.swing);
  document.getElementById('panel2Title').textContent = 'Swing Channel 4H';
  document.getElementById('panel2Sub').textContent = panelSubtitle(data.channel);
  document.getElementById('panel3Title').textContent = '4HGERI';
  document.getElementById('panel3Sub').textContent = panelSubtitle(data.geri);

  function display(value, fallback = 'SIN_DATO') {{
    return value == null || value === '' ? fallback : String(value);
  }}

  function decimal(value, digits = 2) {{
    return Number.isFinite(value) ? Number(value).toFixed(digits) : 'SIN_DATO';
  }}

  function money(value) {{
    return Number.isFinite(value) ? `$${{Number(value).toFixed(2)}}` : 'SIN_DATO';
  }}

  function dateTime(value) {{
    if (!value) return 'SIN_DATO';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : d3.utcFormat('%d/%m/%Y %H:%M UTC')(parsed);
  }}

  function indicatorRows(container, rows) {{
    container.replaceChildren();
    rows.forEach(([label, value]) => {{
      const term = document.createElement('dt');
      const detail = document.createElement('dd');
      term.textContent = label;
      detail.textContent = value;
      container.append(term, detail);
    }});
  }}

  const divergence = data.divergence || {{ availability: 'SIN_DATO', state: 'SIN_DATO' }};
  divergenceBadge.textContent = display(divergence.state);
  const pivot1 = divergence.price_pivot_1 == null
    ? 'SIN_DATO'
    : `${{money(divergence.price_pivot_1)}} · ${{dateTime(divergence.price_pivot_1_at)}}`;
  const pivot2 = divergence.price_pivot_2 == null
    ? 'SIN_DATO'
    : `${{money(divergence.price_pivot_2)}} · ${{dateTime(divergence.price_pivot_2_at)}}`;
  indicatorRows(divergenceMetrics, [
    ['Estado / veredicto', `${{display(divergence.state)}} · ${{display(divergence.verdict)}}`],
    ['Dirección / score', `${{display(divergence.direction)}} · ${{decimal(divergence.score, 1)}}/100`],
    ['Pivote 1', pivot1],
    ['Pivote 2', pivot2],
    ['Cambio de precio', divergence.price_change_percent == null ? 'SIN_DATO' : `${{decimal(divergence.price_change_percent, 2)}}%`],
    ['Mejora OBV normalizada', decimal(divergence.obv_improvement_normalized, 2)],
    ['Separación', divergence.pivot_separation_weeks == null ? 'SIN_DATO' : `${{decimal(divergence.pivot_separation_weeks, 0)}} semanas`],
    ['Reclaim', money(divergence.reclaim_trigger)],
    ['Invalidación', money(divergence.invalidation)],
    ['Datos al', dateTime(divergence.as_of)],
  ]);
  if (divergence.availability === 'SIN_DATO') {{
    divergenceNote.textContent = 'SIN_DATO: el engine no publicó un payload vigente; no se interpreta como evidencia bajista.';
  }} else {{
    const reasons = Array.isArray(divergence.reasons) ? divergence.reasons.join(' · ') : '';
    divergenceNote.textContent = `${{reasons || 'Sin razones adicionales publicadas.'}} La divergencia sólo aporta contexto de posible absorción; no identifica compradores ni crea una entrada.`;
  }}

  const gamma = data.gamma || {{ availability: 'SIN_DATO', status: 'SIN_DATO', freshness: 'SIN_DATO' }};
  gammaBadge.textContent = `${{display(gamma.status)}} · ${{display(gamma.freshness)}}`;
  indicatorRows(gammaMetrics, [
    ['Vigencia', `${{dateTime(gamma.generated_at)}} → ${{dateTime(gamma.expires_at)}}`],
    ['Calidad / cobertura', `${{decimal(gamma.quality_score, 1)}}/100 · ${{gamma.coverage_ratio == null ? 'SIN_DATO' : `${{decimal(gamma.coverage_ratio * 100, 1)}}%`}}`],
    ['Contratos útiles', gamma.usable_contract_count == null ? 'SIN_DATO' : `${{decimal(gamma.usable_contract_count, 0)}}/${{decimal(gamma.contract_count, 0)}}`],
    ['Régimen / sesgo', `${{display(gamma.gamma_regime)}} · ${{display(gamma.directional_bias)}}`],
    ['Ratio gamma neto', decimal(gamma.net_gamma_ratio, 3)],
    ['Call / put wall', `${{money(gamma.call_wall)}} · ${{money(gamma.put_wall)}}`],
    ['Absolute wall', money(gamma.absolute_gamma_wall)],
    ['Max pain / gamma flip', `${{money(gamma.max_pain)}} · ${{money(gamma.gamma_flip)}}`],
    ['Expected move', `${{money(gamma.expected_move_low)}} - ${{money(gamma.expected_move_high)}}`],
    ['Riesgo pin / aceleración', `${{gamma.pin_risk ? 'SÍ' : 'NO'}} · ${{gamma.acceleration_risk ? 'SÍ' : 'NO'}}`],
    ['OI al', display(gamma.open_interest_as_of)],
  ]);
  const gammaWarningList = Array.isArray(gamma.warnings) ? gamma.warnings : [];
  if (gamma.availability === 'SIN_DATO') {{
    gammaWarnings.textContent = 'SIN_DATO: el engine no publicó un payload; no se interpreta como evidencia bajista.';
  }} else {{
    const freshnessWarning = gamma.freshness === 'VIGENTE' ? '' : 'Payload vencido. ';
    gammaWarnings.textContent = `${{freshnessWarning}}Warnings: ${{gammaWarningList.length ? gammaWarningList.join(' · ') : 'ninguno'}}.`;
  }}

  const series = data.series.map((d) => ({{ t: new Date(d.t), v: d.v }}));
  const dailySeries = data.dailySeries.map((d) => ({{
    t: new Date(d.t), o: d.o, h: d.h, l: d.l, c: d.c, v: d.v,
  }}));
  const xDomain = d3.extent(series, (d) => d.t);
  const allPrices = [
    ...series.map((d) => d.v),
    ...panels.flatMap((panel) => [panel.zoneLow, panel.zoneHigh, panel.support, panel.invalidation].filter((v) => Number.isFinite(v))),
  ];
  const yExtent = d3.extent(allPrices);
  const pad = (yExtent[1] - yExtent[0]) * 0.09;
  const yDomain = [yExtent[0] - pad, yExtent[1] + pad];
  const formatMoney = d3.format('$.2f');
  const formatTime = d3.utcFormat('%H:%M UTC');
  const charts = Array.from(root.querySelectorAll('.chart'));
  const renderers = [];
  let currentStep = 0;
  let playing = true;
  let lastFrame = performance.now();

  function relation(panel, price) {{
    if (price <= panel.invalidation) return 'below invalidation';
    if (price < panel.zoneLow) return 'below zone';
    if (price <= panel.zoneHigh) return 'inside zone';
    return 'above zone';
  }}

  function showTip(event, title, body) {{
    const bounds = root.getBoundingClientRect();
    tooltip.hidden = false;
    tooltip.innerHTML = `<strong>${{title}}</strong><br>${{body}}`;
    tooltip.style.left = `${{Math.max(8, Math.min(bounds.width - 280, event.clientX - bounds.left + 12))}}px`;
    tooltip.style.top = `${{Math.max(8, event.clientY - bounds.top - 58)}}px`;
  }}

  function hideTip() {{
    tooltip.hidden = true;
  }}

  function drawChart(container, panel) {{
    container.innerHTML = '';
    const width = Math.max(320, container.getBoundingClientRect().width || 736);
    const height = width < 560 ? 208 : 228;
    const narrow = width < 560;
    const margin = {{ top: 34, right: narrow ? 18 : 28, bottom: 34, left: narrow ? 58 : 68 }};
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const x = d3.scaleTime().domain(xDomain).range([0, innerW]);
    const y = d3.scaleLinear().domain(yDomain).nice().range([innerH, 0]);
    const svg = d3.select(container).append('svg')
      .attr('viewBox', `0 0 ${{width}} ${{height}}`)
      .attr('role', 'img')
      .attr('aria-label', `${{panel.title}} intraday path`);
    svg.append('title').text(`${{panel.title}} · ${{data.symbol}} intraday`);
    svg.append('desc').text(`Today's price path for ${{data.symbol}} with ${{panel.title}} zone and invalidation levels.`);

    const g = svg.append('g').attr('transform', `translate(${{margin.left}},${{margin.top}})`);
    g.append('rect')
      .attr('data-chart-frame', '')
      .attr('x', 0)
      .attr('y', 0)
      .attr('width', innerW)
      .attr('height', innerH)
      .attr('fill', 'none');

    g.append('g')
      .attr('class', 'axis')
      .attr('transform', `translate(0,${{innerH}})`)
      .call(d3.axisBottom(x).ticks(narrow ? 4 : 6).tickFormat(d3.utcFormat('%H:%M')));
    g.append('g')
      .attr('class', 'axis')
      .call(d3.axisLeft(y).ticks(narrow ? 4 : 5).tickFormat((d) => `$${{Number(d).toFixed(0)}}`));
    g.append('text')
      .attr('class', 'axis-title')
      .attr('data-axis', 'x')
      .attr('x', innerW / 2)
      .attr('y', innerH + 30)
      .attr('text-anchor', 'middle')
      .text('Hora UTC');
    g.append('text')
      .attr('class', 'axis-title')
      .attr('data-axis', 'y')
      .attr('transform', 'rotate(-90)')
      .attr('x', -innerH / 2)
      .attr('y', narrow ? -44 : -54)
      .attr('text-anchor', 'middle')
      .text('Precio (USD)');

    const clipId = `${{panel.key}}-clip`;
    svg.append('defs').append('clipPath').attr('id', clipId).append('rect')
      .attr('x', 0).attr('y', 0)
      .attr('width', 0)
      .attr('height', innerH);

    const plot = g.append('g').attr('clip-path', `url(#${{clipId}})`);
    const line = d3.line().x((d) => x(d.t)).y((d) => y(d.v)).curve(d3.curveMonotoneX);
    plot.append('path').datum(series).attr('class', 'price-line').attr('d', line);

    const hasZone = Number.isFinite(panel.zoneLow) && Number.isFinite(panel.zoneHigh);
    const hasSupport = Number.isFinite(panel.support);
    const hasInvalidation = Number.isFinite(panel.invalidation);
    if (hasZone) {{
      plot.append('rect')
        .attr('class', 'zone-band')
        .attr('x', 0)
        .attr('y', y(panel.zoneHigh))
        .attr('width', innerW)
        .attr('height', Math.max(4, y(panel.zoneLow) - y(panel.zoneHigh)))
        .attr('fill', panel.fill);
      plot.append('line').attr('class', 'zone-edge').attr('x1', 0).attr('x2', innerW).attr('y1', y(panel.zoneLow)).attr('y2', y(panel.zoneLow)).attr('stroke', panel.fill);
      plot.append('line').attr('class', 'zone-edge').attr('x1', 0).attr('x2', innerW).attr('y1', y(panel.zoneHigh)).attr('y2', y(panel.zoneHigh)).attr('stroke', panel.fill);
    }}
    if (hasSupport) {{
      plot.append('line').attr('class', 'support-line').attr('x1', 0).attr('x2', innerW).attr('y1', y(panel.support)).attr('y2', y(panel.support)).attr('stroke', 'var(--viz-series-5)');
    }}
    if (hasInvalidation) {{
      plot.append('line').attr('class', 'invalidation-line').attr('x1', 0).attr('x2', innerW).attr('y1', y(panel.invalidation)).attr('y2', y(panel.invalidation));
    }}

    const guide = plot.append('g');
    guide.append('line').attr('class', 'guide-line').attr('y1', 0).attr('y2', innerH);
    guide.append('circle').attr('class', 'price-point').attr('r', 5.5);

    const hit = g.append('rect')
      .attr('data-chart-hit', '')
      .attr('data-chart-hover-overlay', 'cross-series')
      .attr('x', 0)
      .attr('y', 0)
      .attr('width', innerW)
      .attr('height', innerH)
      .attr('fill', 'transparent');
    hit.on('pointermove', (event) => {{
      const [px] = d3.pointer(event, g.node());
      const idx = d3.bisector((d) => d.t).center(series, x.invert(px));
      update(idx);
      const point = series[idx];
      showTip(event, `${{panel.title}} · ${{formatTime(point.t)}}`, `price ${{formatMoney(point.v)}} · ${{relation(panel, point.v)}} · ${{panel.status}}`);
      step.value = String(idx);
      stepLabel.textContent = `${{idx}}/${{series.length - 1}}`;
    }});
    hit.on('pointerleave', hideTip);

    function update(stepIndex) {{
      const clamped = Math.max(0, Math.min(series.length - 1, stepIndex));
      const point = series[clamped];
      const xPos = x(point.t);
      const yPos = y(point.v);
      svg.select(`#${{clipId}} rect`).attr('width', Math.max(0, xPos));
      guide.select('line').attr('x1', xPos).attr('x2', xPos);
      guide.select('circle').attr('cx', xPos).attr('cy', yPos);
      const zoneText = hasZone ? `zone ${{formatMoney(panel.zoneLow)}}-${{formatMoney(panel.zoneHigh)}}` : 'sin zona publicada';
      const invText = hasInvalidation ? `inv ${{formatMoney(panel.invalidation)}}` : 'sin inval.';
      panel.stateTarget.textContent = `${{formatTime(point.t)}} · ${{panel.title}}: ${{relation(panel, point.v)}} · price ${{formatMoney(point.v)}} · ${{zoneText}} · ${{invText}}`;
    }}

    return {{ update }};
  }}

  function dailyPivots(values) {{
    const pivots = [];
    let priorHigh = null;
    let priorLow = null;
    for (let index = 1; index < values.length - 1; index += 1) {{
      const previous = values[index - 1];
      const current = values[index];
      const next = values[index + 1];
      if (current.h > previous.h && current.h >= next.h) {{
        const label = priorHigh == null ? 'H' : (current.h > priorHigh ? 'HH' : 'LH');
        pivots.push({{ index, value: current.h, kind: 'high', label }});
        priorHigh = current.h;
      }}
      if (current.l < previous.l && current.l <= next.l) {{
        const label = priorLow == null ? 'L' : (current.l > priorLow ? 'HL' : 'LL');
        pivots.push({{ index, value: current.l, kind: 'low', label }});
        priorLow = current.l;
      }}
    }}
    return pivots;
  }}

  function drawDailyChart(container) {{
    container.innerHTML = '';
    if (!dailySeries.length) {{
      stateDaily.textContent = 'Sin ruedas diarias disponibles';
      return;
    }}
    const width = Math.max(320, container.getBoundingClientRect().width || 736);
    const height = width < 560 ? 300 : 350;
    const narrow = width < 560;
    const margin = {{ top: 28, right: narrow ? 18 : 34, bottom: 40, left: narrow ? 58 : 68 }};
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const levelValues = panels.flatMap((panel) => [panel.zoneLow, panel.zoneHigh, panel.support])
      .filter((value) => Number.isFinite(value));
    const extent = d3.extent([
      ...dailySeries.flatMap((bar) => [bar.l, bar.h]),
      ...levelValues,
    ]);
    const span = Math.max(0.01, extent[1] - extent[0]);
    const y = d3.scaleLinear()
      .domain([extent[0] - span * 0.08, extent[1] + span * 0.08])
      .nice()
      .range([innerH, 0]);
    const x = d3.scaleBand()
      .domain(dailySeries.map((_, index) => index))
      .range([0, innerW])
      .padding(0.28);
    const svg = d3.select(container).append('svg')
      .attr('viewBox', `0 0 ${{width}} ${{height}}`)
      .attr('role', 'img')
      .attr('aria-label', `${{data.symbol}} estructura diaria de 21 ruedas`);
    svg.append('title').text(`${{data.symbol}} · estructura diaria y niveles de los tres motores Swing`);
    svg.append('desc').text('Velas diarias con pivotes de estructura, soportes y zonas de entrada de Swing diario, Swing Channel 4H y 4HGERI.');
    const g = svg.append('g').attr('transform', `translate(${{margin.left}},${{margin.top}})`);
    g.append('rect')
      .attr('data-chart-frame', '')
      .attr('x', 0).attr('y', 0)
      .attr('width', innerW).attr('height', innerH)
      .attr('fill', 'none');
    const tickIndexes = dailySeries
      .map((_, index) => index)
      .filter((index) => index === dailySeries.length - 1 || index % (narrow ? 4 : 2) === 0);
    g.append('g')
      .attr('class', 'axis')
      .attr('transform', `translate(0,${{innerH}})`)
      .call(d3.axisBottom(x).tickValues(tickIndexes).tickFormat((index) => d3.utcFormat('%d/%m')(dailySeries[index].t)));
    g.append('g')
      .attr('class', 'axis')
      .call(d3.axisLeft(y).ticks(narrow ? 5 : 7).tickFormat((value) => `$${{Number(value).toFixed(2)}}`));
    g.append('text')
      .attr('class', 'axis-title').attr('data-axis', 'x')
      .attr('x', innerW / 2).attr('y', innerH + 34)
      .attr('text-anchor', 'middle').text('Rueda (día/mes)');
    g.append('text')
      .attr('class', 'axis-title').attr('data-axis', 'y')
      .attr('transform', 'rotate(-90)')
      .attr('x', -innerH / 2).attr('y', narrow ? -45 : -54)
      .attr('text-anchor', 'middle').text('Precio diario (USD)');

    panels.forEach((panel) => {{
      if (Number.isFinite(panel.zoneLow) && Number.isFinite(panel.zoneHigh)) {{
        const bandTop = y(panel.zoneHigh);
        const bandBottom = y(panel.zoneLow);
        g.append('rect')
          .attr('class', 'daily-zone')
          .attr('x', 0).attr('width', innerW)
          .attr('y', bandTop)
          .attr('height', Math.max(3, bandBottom - bandTop))
          .attr('fill', panel.fill);
        g.append('line')
          .attr('class', 'daily-zone-edge')
          .attr('x1', 0).attr('x2', innerW)
          .attr('y1', bandTop).attr('y2', bandTop)
          .attr('stroke', panel.fill);
        g.append('line')
          .attr('class', 'daily-zone-edge')
          .attr('x1', 0).attr('x2', innerW)
          .attr('y1', bandBottom).attr('y2', bandBottom)
          .attr('stroke', panel.fill);
        g.append('text')
          .attr('class', 'daily-zone-label')
          .attr('x', innerW - 6)
          .attr('y', bandTop + 13)
          .attr('text-anchor', 'end')
          .text(`${{panel.title}} · ${{formatMoney(panel.zoneLow)}}-${{formatMoney(panel.zoneHigh)}}`);
      }}
      if (Number.isFinite(panel.support)) {{
        g.append('line')
          .attr('class', 'daily-support')
          .attr('x1', 0).attr('x2', innerW)
          .attr('y1', y(panel.support)).attr('y2', y(panel.support))
          .attr('stroke', panel.fill)
          .attr('stroke-dasharray', panel.key === 'swing' ? null : (panel.key === 'channel' ? '8 4' : '3 4'));
      }}
    }});

    const candles = g.append('g');
    candles.selectAll('line.daily-wick')
      .data(dailySeries).join('line')
      .attr('class', 'daily-wick')
      .attr('x1', (_, index) => x(index) + x.bandwidth() / 2)
      .attr('x2', (_, index) => x(index) + x.bandwidth() / 2)
      .attr('y1', (bar) => y(bar.h)).attr('y2', (bar) => y(bar.l))
      .attr('stroke', (bar) => bar.c >= bar.o ? 'var(--green)' : 'var(--red)');
    candles.selectAll('rect.daily-body')
      .data(dailySeries).join('rect')
      .attr('class', 'daily-body')
      .attr('x', (_, index) => x(index))
      .attr('width', x.bandwidth())
      .attr('y', (bar) => y(Math.max(bar.o, bar.c)))
      .attr('height', (bar) => Math.max(2, Math.abs(y(bar.o) - y(bar.c))))
      .attr('fill', (bar) => bar.c >= bar.o ? 'var(--green)' : 'var(--red)')
      .attr('stroke', (bar) => bar.c >= bar.o ? 'var(--green)' : 'var(--red)');

    const pivots = dailyPivots(dailySeries);
    g.append('g').selectAll('text')
      .data(pivots.slice(-6)).join('text')
      .attr('class', 'pivot-label')
      .attr('x', (pivot) => x(pivot.index) + x.bandwidth() / 2)
      .attr('y', (pivot) => y(pivot.value) + (pivot.kind === 'high' ? -7 : 14))
      .attr('text-anchor', 'middle')
      .text((pivot) => pivot.label);

    const guide = g.append('line')
      .attr('data-chart-hover-guide', '')
      .attr('class', 'guide-line')
      .attr('y1', 0).attr('y2', innerH)
      .attr('visibility', 'hidden');
    const hit = g.append('rect')
      .attr('data-chart-hit', '')
      .attr('data-chart-hover-overlay', 'cross-series')
      .attr('x', 0).attr('y', 0)
      .attr('width', innerW).attr('height', innerH)
      .attr('fill', 'transparent');
    hit.on('pointermove', (event) => {{
      const [pointerX] = d3.pointer(event, g.node());
      const index = Math.max(0, Math.min(dailySeries.length - 1, Math.round((pointerX / innerW) * (dailySeries.length - 1))));
      const bar = dailySeries[index];
      const guideX = x(index) + x.bandwidth() / 2;
      guide.attr('x1', guideX).attr('x2', guideX).attr('visibility', 'visible');
      showTip(
        event,
        `${{data.symbol}} · ${{d3.utcFormat('%d/%m/%Y')(bar.t)}}`,
        `O ${{formatMoney(bar.o)}} · H ${{formatMoney(bar.h)}} · L ${{formatMoney(bar.l)}} · C ${{formatMoney(bar.c)}}`
      );
      stateDaily.textContent = `Rueda ${{d3.utcFormat('%d/%m/%Y')(bar.t)}} · rango ${{formatMoney(bar.l)}}-${{formatMoney(bar.h)}} · cierre ${{formatMoney(bar.c)}}`;
    }});
    hit.on('pointerleave', () => {{ guide.attr('visibility', 'hidden'); hideTip(); }});

    const latestHigh = [...pivots].reverse().find((pivot) => pivot.kind === 'high');
    const latestLow = [...pivots].reverse().find((pivot) => pivot.kind === 'low');
    const structureParts = [];
    if (latestHigh) structureParts.push(`máximo ${{latestHigh.label}} ${{formatMoney(latestHigh.value)}}`);
    if (latestLow) structureParts.push(`mínimo ${{latestLow.label}} ${{formatMoney(latestLow.value)}}`);
    dailyStructure.textContent = structureParts.length ? structureParts.join(' · ') : 'estructura aún sin pivotes confirmados';
    const last = dailySeries[dailySeries.length - 1];
    stateDaily.textContent = `Última rueda ${{d3.utcFormat('%d/%m/%Y')(last.t)}} · O ${{formatMoney(last.o)}} · H ${{formatMoney(last.h)}} · L ${{formatMoney(last.l)}} · C ${{formatMoney(last.c)}}`;
  }}

  dailyLegend.innerHTML = panels.map((panel) => {{
    const support = Number.isFinite(panel.support) ? formatMoney(panel.support) : 'sin soporte';
    const entry = Number.isFinite(panel.zoneLow) && Number.isFinite(panel.zoneHigh)
      ? `${{formatMoney(panel.zoneLow)}}-${{formatMoney(panel.zoneHigh)}}`
      : 'sin entrada';
    return `<span class="legend-item"><span class="legend-swatch" style="--legend-color:${{panel.fill}}"></span>${{panel.title}} · soporte ${{support}} · entrada ${{entry}}</span>`;
  }}).join('');

  function redrawCharts() {{
    renderers.splice(0, renderers.length);
    renderers.push(drawChart(charts[0], panels[0]));
    renderers.push(drawChart(charts[1], panels[1]));
    renderers.push(drawChart(charts[2], panels[2]));
    drawDailyChart(root.querySelector('.daily-chart'));
  }}

  redrawCharts();

  function sync(stepIndex) {{
    const point = series[stepIndex];
    const delta = ((point.v / data.firstPrice) - 1) * 100;
    summary.textContent = `${{data.symbol}} ${{formatMoney(data.firstPrice)}} → ${{formatMoney(point.v)}} (${{delta >= 0 ? '+' : ''}}${{delta.toFixed(2)}}%)`;
    range.textContent = `serie intradía: ${{series.length}} puntos · latest trade ${{formatMoney(data.latestPrice)}}`;
    asOf.textContent = `as of ${{formatTime(point.t)}}`;
    stepLabel.textContent = `${{stepIndex}}/${{series.length - 1}}`;
    renderers.forEach((renderer) => renderer.update(stepIndex));
  }}

  step.max = String(series.length - 1);
  step.addEventListener('input', () => {{
    playing = false;
    playBtn.textContent = 'Play';
    sync(Number(step.value));
  }});
  playBtn.addEventListener('click', () => {{
    playing = !playing;
    playBtn.textContent = playing ? 'Pause' : 'Play';
    if (playing) {{
      lastFrame = performance.now();
      requestAnimationFrame(tick);
    }}
  }});

  function tick(now) {{
    if (!playing) return;
    if (now - lastFrame >= 130) {{
      lastFrame = now;
      const next = Math.min(series.length - 1, Number(step.value) + 1);
      step.value = String(next);
      sync(next);
      if (next >= series.length - 1) {{
        playing = false;
        playBtn.textContent = 'Replay';
        return;
      }}
    }}
    requestAnimationFrame(tick);
  }}

  sync(0);
  requestAnimationFrame(tick);
  let observedWidth = root.getBoundingClientRect().width;
  new ResizeObserver(() => {{
    const nextWidth = root.getBoundingClientRect().width;
    if (Math.abs(nextWidth - observedWidth) < 1) return;
    observedWidth = nextWidth;
    redrawCharts();
    sync(Number(step.value));
  }}).observe(root);
}})();
</script>"""


async def build_html(symbol: str, output_dir: Path) -> Path:
    settings = AppSettings()
    now = datetime.now(UTC)
    start = _session_open_utc(now)
    series, daily_series, snapshot = await _fetch_market_series(
        settings, symbol, start, now
    )
    sampled = _sample_series(series)
    if not sampled:
        raise RuntimeError(f"no intraday bars returned for {symbol}")

    async def fetch_assessment(
        bus: NatsJetStreamEventBus, subject: str
    ) -> dict[str, object] | None:
        envelope = await bus.get_last(subject)
        if envelope is None:
            return None
        return _as_mapping(envelope.payload)

    bus = await NatsJetStreamEventBus.connect(servers=[settings.nats_url.get_secret_value()])
    try:
        swing = await fetch_assessment(
            bus, analysis_result_subject(AnalysisHorizon.SWING, symbol)
        )
        channel = await fetch_assessment(bus, swing_channel_assessment_subject(symbol))
        geri = await fetch_assessment(bus, geri_assessment_subject(symbol))
        divergence = await fetch_assessment(
            bus, analysis_result_subject(AnalysisHorizon.VOLUME_STRUCTURE, symbol)
        )
        gamma = await fetch_assessment(bus, options_gamma_assessment_subject(symbol))
    finally:
        await bus.close()

    if swing is None:
        raise RuntimeError(
            f"missing live assessment for subject {analysis_result_subject(AnalysisHorizon.SWING, symbol)}"
        )

    latest_trade_value = snapshot.get("latestTrade")
    latest_trade = (
        _as_mapping(latest_trade_value) if latest_trade_value is not None else {}
    )
    first_price = float(str(sampled[0]["v"]))
    data: dict[str, object] = {
        "symbol": symbol.upper(),
        "asOf": now.isoformat().replace("+00:00", "Z"),
        "firstPrice": first_price,
        "latestPrice": float(str(latest_trade.get("p", sampled[-1]["v"]))),
        "latestTradeTime": latest_trade.get("t"),
        "series": sampled,
        "dailySeries": [
            {
                "t": bar["t"],
                "o": float(str(bar["o"])),
                "h": float(str(bar["h"])),
                "l": float(str(bar["l"])),
                "c": float(str(bar["c"])),
                "v": float(str(bar.get("v", 0))),
            }
            for bar in daily_series
        ],
        "swing": {
            "status": str(swing.get("verdict", "WATCH")),
            "zone_low": _float_metric(swing, "entry_zone_low"),
            "zone_high": _float_metric(swing, "entry_zone_high"),
            "support": _float_metric(swing, "support"),
            "invalidation": _float_metric(swing, "invalidation"),
        },
        "channel": {
            "status": str(channel.get("maturity", "SIN_EMISION")) if channel is not None else "SIN_EMISION",
            "zone_low": _as_float(channel.get("zone_low")) if channel is not None else None,
            "zone_high": _as_float(channel.get("zone_high")) if channel is not None else None,
            "support": _as_float(channel.get("support")) if channel is not None else None,
            "invalidation": _as_float(channel.get("invalidation")) if channel is not None else None,
        },
        "geri": {
            "status": str(geri.get("maturity", "SIN_EMISION")) if geri is not None else "SIN_EMISION",
            "zone_low": _as_float(geri.get("zone_low")) if geri is not None else None,
            "zone_high": _as_float(geri.get("zone_high")) if geri is not None else None,
            "support": (
                _as_float(geri.get("active_level_price"))
                if geri is not None and str(geri.get("active_level_kind")) == "SUPPORT"
                else None
            ),
            "invalidation": _as_float(geri.get("invalidation")) if geri is not None else None,
        },
        "divergence": _divergence_indicator(divergence),
        "gamma": _gamma_indicator(
            gamma, now_iso=now.isoformat().replace("+00:00", "Z")
        ),
    }
    title = f"{symbol.upper()} · recorrido de hoy frente a Swing, Swing Channel 4H y 4HGERI"
    fragment = _build_template(title, data)
    output_dir.mkdir(parents=True, exist_ok=True)
    fragment_path = output_dir / f"{symbol.lower()}-three-engine-day-path.html"
    fragment_path.write_text(fragment, encoding="utf-8")

    preview_path = output_dir / f"{symbol.lower()}-three-engine-day-path-preview.html"
    render_candidates = [
        Path.home() / ".codex" / "plugins" / "cache" / "openai-bundled" / "visualize" / "1.0.21" / "skills" / "visualize" / "scripts" / "render.py",
        Path.home() / ".codex" / "plugins" / "cache" / "openai-bundled" / "visualize" / "1.0.20" / "skills" / "visualize" / "scripts" / "render.py",
        Path("/mnt/c/Users/lgonz/.codex/plugins/cache/openai-bundled/visualize/1.0.21/skills/visualize/scripts/render.py"),
        Path("/mnt/c/Users/lgonz/.codex/plugins/cache/openai-bundled/visualize/1.0.20/skills/visualize/scripts/render.py"),
        Path("/mnt/c/Users/lgonz/.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/visualize/skills/visualize/scripts/render.py"),
    ]
    render_script = next((candidate for candidate in render_candidates if candidate.exists()), None)
    if render_script is None:
        raise FileNotFoundError("could not locate visualize render.py")
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, str(render_script), str(fragment_path), str(preview_path)],
        check=True,
    )
    return preview_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an animated intraday comparison for Swing, Swing Channel 4H and 4HGERI."
    )
    parser.add_argument("ticker", help="Ticker symbol to visualize")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / ".runtime" / "engine-animations"),
        help="Directory where the HTML files are written",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(build_html(args.ticker, Path(args.output_dir)))


if __name__ == "__main__":
    main()
