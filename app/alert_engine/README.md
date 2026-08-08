# Alert engine

The alert engine keeps the newest `AnalysisResult` for each symbol and horizon, filters stale
components, combines fresh LONG_TERM, SWING, and INTRADAY direction with configurable
weights, and attaches DILUTION only as an informational risk warning. SEC results never change
the directional score, suppress an alert, or invalidate an entry.

Default severity thresholds are WATCH 60, ACTION 75, and CRITICAL 90. Fresh LONG_TERM, SWING,
and INTRADAY results are required; DILUTION is optional. Inputs in the future are rejected. Alert expiry,
freshness limits, cooldown windows, and weights are explicit policy values. Escalation can
bypass cooldown; otherwise the same symbol/direction is suppressed until the next window.

`AlertEngine.ingest(result, now=...)` returns a `LocalAlert` or `None`. It never submits an
order and contains no account, position, sizing, or Trading API concept. `AlertDispatcher`
sends an alert to local sinks and can publish it through a structural port using
`LOCAL_ALERT_EVENT` and `local_alert_subject`.

`AlertEngineV3` is the active distributed generation. It preserves every V2 alert and adds an
L2 Swing-continuation path that does not require a Long thesis: two distinct strong Intraday
readings in the same New York market date, separated by the configured delay and both reporting
a five-minute higher low, confirm the still-fresh Swing setup once per symbol/session.

`AlertEngineV2` remains available for replay. It emits explicit `AlertKind` values:

- `LONG_BUY_ZONE` from a fresh bullish Long result;
- `SWING_SETUP` from a fresh bullish Swing result;
- `ENTRY_CONFIRMED` when Intraday confirms either Long or Swing;
- `HIGH_CONVICTION_BUY` when Long, Swing, and Intraday are all bullish;
- `SEC_WARNING` independently, without gating another alert.

The standalone Alert process consumes `marketbot.v1.analysis.result.>` and
`marketbot.v1.entry-watch.transition.>` through separate durable NATS consumers and publishes
final `LocalAlert` events for viewers. It does not read any engine store or the watcher database.

Included local sinks:

- `ConsoleAlertSink`: multi-line actionable context with price, entry zone, invalidation,
  objectives, per-horizon verdicts, technical metrics, reasons, and an optional terminal bell;
- `NdjsonAlertSink`: canonical, fsynced, append-only NDJSON rotated by `America/New_York` market
  date, with independent recovery and deduplication for each daily ledger. It never deletes old
  ledgers.

`LocalAlert.component_analyses` embeds the exact fresh `AnalysisResult` values used by the alert.
This keeps the structured NDJSON/event payload suitable for future dashboards while the console
formatter presents the most useful Stock Analyzer-style fields: weekly SMA200 distance, Long
setup/entry scores, Swing anchored VWAPs and 2R target, Intraday VWAP/RVOL/R:R, and SEC evidence.
Entry Watch alerts additionally carry their frozen zone and invalidation as alert metrics. None of
these presentation fields express or submit an order.

Windows toast can be added later as an optional sink without changing the engine or tests.

Run the focused suite with:

```powershell
uv run pytest app/alert_engine/tests
```
