# MarketBot Geometry Viewer

The exporter downloads completed split-adjusted `1Day` and regular-session `15Min`
bars directly from Alpaca. It aggregates completed 4H RTH segments locally and uses the
pure engines selected by the current MarketBot definition to calculate:

- daily Swing zone, support, invalidation, resistance, liquidity, target and AVWAPs;
- SwingTrade impulse, Fibonacci 61.8%-50% entry zone, 161.8% extension, 20-day
  support/resistance and targets;
- 4HGERI active structural level, zone and invalidation when geometry is available.

CSV schema v2 also identifies the meaning and actionability of every layer:

- Swing Diario exports verdict, direction, score and `swing_entry_gate_passed`.
- SwingTrade exports `ENGINE_ASSESSMENT` or `ENGINE_REJECTED`, maturity, eligibility and reasons.
- 4HGERI exports maturity, trade side, active sequence and bounce/15m/4H confirmations.

The exporter only publishes SwingTrade price levels when the operational engine emits
an assessment. If the engine rejects the LONG impulse or any other gate required to
construct the thesis, the status is `ENGINE_REJECTED`, every SwingTrade price field is
zero, and Pine draws no SwingTrade line.

It does not connect to NATS/JetStream, read prior assessments or reconstruct
Entry Watcher / Entry Opportunity state.

A GERI resistance remains available as a fuchsia structural line but is never filled as
a purple LONG buy zone; only valid GERI support geometry is filled.

From the repository root:

```bash
uv run python scripts/export_marketbot_tradingview.py HUT XLI
```

Paste the emitted header and rows into `marketbot_operational_viewer.pine`. The Pine
script selects the row matching the open chart ticker. Schema v2 is embedded in the
viewer, so the pasted header is optional and is ignored when present. The dashboard must
show `CSV / ESQUEMA = OK`; otherwise no engine layer is considered loaded. Re-export the
CSV whenever you want a fresh Alpaca snapshot.

Layer visibility can be controlled at two levels: **Inputs** enables or disables each
complete engine, while **Style** exposes every individual zone boundary, support,
invalidation, target, AVWAP and GERI N-level. The importer accepts comma-separated CSV,
tab-separated text copied from a spreadsheet, or semicolon-separated rows.

If a module cannot calculate valid geometry, its numeric fields remain `0` and the CLI
writes the exact rejection reason to stderr. A missing layer is therefore explicit and
is never replaced with an invented price.

## Colors

- Swing zone: green; support: aqua; invalidation: red; resistance: purple;
  liquidity: fuchsia; target: green.
- Pivot AVWAP: yellow; breakout AVWAP: orange.
- SwingTrade Fibonacci zone: blue; impulse bounds: gray/silver; 161.8% extension:
  teal.
- 4HGERI zone: purple; active support: aqua; active resistance: fuchsia.
