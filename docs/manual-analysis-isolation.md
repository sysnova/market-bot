# Manual analysis and continuous monitoring

The analyzer CLI and Dashboard's request-analysis action launch a private Python
process. The selected ticker and per-engine timeout are arguments; stdout returns
one JSON report. Cancellation terminates the child. No shell is involved.

The child constructs engines with `MarketBotAssembly`, using the configured
implementation and strategy versions. Core runs first, independent engines run
concurrently, and Signal Fusion receives only results from this invocation.
The existing engine selection and holdings/allocation gates remain in effect;
Portfolio Flow requires a live window, and Peter Lynch and SEC remain excluded.

Market bars are fetched through the existing Alpaca read-only adapters. Secondary
engines reuse MarketHistoryService and MarketHistoryLoader with a private memory
repository. PostgreSQL reads supply holdings, allocations and rotation profiles.
Manual evaluation does not save decisions, alerts, rotation results, or watchlist
additions. State, entry watches, and opportunities belong to this invocation.
No shared Redis decision cache is configured in the child.

NATS is neither an input nor an output of the manual report. The compatibility
`--nats` flag does not enable publication. The old live service also refuses to
publish universe changes for `manual-symbols`; one-shot and explicitly scoped
live compositions do not receive a universe publisher.

The Dashboard's live ticker subscription continues independently. Manual reports
are shown as separate, timestamped results with per-engine status and full output.
They do not overwrite live assessments or make stale operational evidence fresh.

Previously the analyzer reused production service launchers. Core initialization
published a global universe change, secondary launchers could write operational
state, and Fusion/Patreon/Long Portfolio hydrated results from NATS. A one-ticker
analysis could therefore change monitoring for unrelated symbols. This path is
no longer used by manual analysis.

Deployment requires updating the runtime checkout and restarting affected services.
Changing the Windows checkout alone does not repair an already-running WSL process.
After deployment verify the effective watchlist and new Core publications for
several symbols, rather than relying only on startup ready files.
