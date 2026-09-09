# Early SHORT breakdown confirmation

Intraday 8.0.0 / strategy 1.4.0 adds EARLY_BREAKDOWN. A standard-quality candle
can confirm a bearish breakdown when there is a completed five-minute lower
high, bearish regime, raw short confirmation, an efficient entry and acceptable
risk, with five-minute momentum <= -0.50% and relative volume >= 1.30.
Weak candles, VWAP-rejection setups, extended entries and missing structure do
not qualify for this new lane. Existing strong and displacement lanes remain.
These are initial policy thresholds, not statistically optimized parameters.

The candle quality remains `standard`; the engine does not pretend a mature
retest occurred. The new lane sets the overall confirmation gate and retains
`short_mature_retest_confirmed=false`. Alert 3.9 still independently requires a
fresh bearish Swing assessment with `short_structure_gate_passed=true`, an
identified setup, and valid entry/stop/target geometry. No orders are submitted.
Quality and lower-high blockers now have distinct reasons; missing quality is
reported as `wait_short_quality`, rather than an unexplained pending retest.

## Reproduction

The fixture is Alpaca SIP, split-adjusted 1-minute OHLCV for ASTS, 2026-09-09,
13:30–14:20 UTC (51 bars), retrieved on the same date. It is an offline fixture,
not a live feed. `tests/short_replay.py` feeds only the current regular session
and aggregates completed 5-minute bars using the production aggregator. No
future bar is supplied to an earlier evaluation.

Within this sample only 14:14 UTC changes from WATCH in v7 to FAVORABLE in v8:
reference 64.255, trigger 64.2700, invalidation 64.4156, objective 64.0140,
momentum -0.5187%, RVOL 1.4407, quality standard. The bar is available after
completion, at approximately 14:15 UTC, not at its start timestamp.
The integration test supplies the observed 13:45 UTC Swing gate and verifies
SHORT CONFIRMED; removing that gate prevents confirmation. This is a replay
with reconstructed Swing fields, not proof of historical live delivery.

Subsequent bars reach the objective at 14:18 UTC without first touching the
proposed stop. This is OHLC path evidence; it does not model entry execution,
spread, slippage, borrow costs, or general profitability. Broader out-of-sample
validation is still needed before claiming improved expectancy.

## Deployment boundary

Definition 7.54.0 is based on the observed live 7.50.0 assembly and changes only
Intraday to 8.0.0 / strategy 1.4.0. It deliberately excludes other pending Windows
work (including 7.53.0). Prior versions remain available for rollback.
No default definition, WSL files, running processes, watchlists or execution
settings were changed by this implementation. WSL installation and activation
require the user's explicit permission under the workspace WSL rule.

For deployment, apply only the Intraday v8 module, its export and strategy adapter,
the single catalog registration, and the two new YAML artifacts to the WSL
checkout; do not copy the whole dirty Windows checkout or its engine_catalog.py.
Activate the new definition through the normal runtime procedure after verifying
the live assembly still matches the 7.50.0 baseline. Rollback selects 7.50.0.
