# Alert engine

The alert engine keeps the newest `AnalysisResult` for each symbol and horizon, filters stale
components, combines fresh LONG_TERM, SWING, and INTRADAY direction with configurable
weights, and treats DILUTION as a risk overlay. A dilution `CAUTION` applies a weighted
penalty; `AVOID` vetoes a bullish composite and produces a critical human warning.

Default severity thresholds are WATCH 60, ACTION 75, and CRITICAL 90. At least three fresh
horizons, including DILUTION, are required. Inputs in the future are rejected. Alert expiry,
freshness limits, cooldown windows, and weights are explicit policy values. Escalation can
bypass cooldown; otherwise the same symbol/direction is suppressed until the next window.

`AlertEngine.ingest(result, now=...)` returns a `LocalAlert` or `None`. It never submits an
order and contains no account, position, sizing, or Trading API concept. `AlertDispatcher`
sends an alert to local sinks and can publish it through a structural port using
`LOCAL_ALERT_EVENT` and `local_alert_subject`.

Included local sinks:

- `ConsoleAlertSink`: readable single-line console notification with an optional terminal bell;
- `NdjsonAlertSink`: canonical, fsynced, append-only NDJSON with deduplication by alert key.

Windows toast can be added later as an optional sink without changing the engine or tests.

Run the focused suite with:

```powershell
uv run pytest app/alert_engine/tests
```
