# SEC dilution engine ownership

- This folder owns the deterministic SEC dilution policy, its tests, fixtures, and focused docs.
- The core receives normalized filing metadata, document signals, and CompanyFacts snapshots; it must never perform network or filesystem I/O.
- Do not import any other engine. Cross-engine output uses only `app/contracts/AnalysisResult`.
- Keep scoring additive, capped at 100, order-independent, and traceable to structured evidence.
- A missing dataset is `UNKNOWN`, not `LOW`. Stale filings beyond the documented lookback do not contribute risk.
- Document-signal extraction and SEC HTTP clients belong in adapters outside this core.
- The EDGAR adapter requires a configured contact-bearing User-Agent and accepts CIK only; never add per-tick SEC access or implicit ticker discovery.
- Metadata descriptions are not document evidence. Text signals require the bounded parser/provider port.
- Add a failing unit test before changing policy weights or severity thresholds.
