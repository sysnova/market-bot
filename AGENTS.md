# MarketBot repository guide

## Structure and ownership

- Every independently evolving engine lives in `app/<engine>/`.
- An engine owns its implementation, tests, fixtures, and focused documentation inside that folder.
- Shared technical primitives may live in `app/common/`; business rules may not.
- Stable cross-engine messages and ports live in `app/contracts/` and require compatibility review.
- Repository-wide configuration, dependency resolution, CI, and integration policy are owned at the root.
- There is one root `pyproject.toml` and one root `uv.lock`. Engines must not create nested environments or dependency manifests.

## Dependency boundaries

- Engines must not import another engine's package.
- Collaboration occurs only through `app/contracts/` and external adapters such as NATS or PostgreSQL.
- `app/common/` must not import from any engine.
- The root is the unique integrator: it composes engines, owns cross-engine checks, and resolves shared dependency versions.
- Do not hide a circular dependency behind dynamic imports. Extract an explicit contract instead.

## Development policy

- Target Python `>=3.14,<3.15` and use `uv` for environments, locking, and commands.
- Add a failing test before implementation. Keep unit tests isolated from network, databases, clocks, and entropy through injection.
- Put engine-specific tests and docs under that engine's folder. Root `docs/` is reserved for repository-wide architecture and operations.
- Mark tests that need services with `@pytest.mark.integration`; ordinary unit tests must need no secrets or running containers.
- Before handoff run `uv run ruff check .`, `uv run pyright`, and `uv run pytest`.
- Never commit credentials. Configuration exposed in diagnostics must redact `SecretStr` values.

## Runtime data sources

- Do not query Supabase, its dashboard, Data API, MCP server, or remote projects for this repository unless the user explicitly requests Supabase.
- Runtime persistence is local PostgreSQL configured by `MARKETBOT_DATABASE_URL` and local NATS JetStream configured by `MARKETBOT_NATS_URL`/`MARKETBOT_*` settings.
- Treat files under `supabase/migrations/` as versioned PostgreSQL schema artifacts; their presence does not authorize or imply a Supabase lookup.
- For persisted analytical events, inspect the local `MARKETBOT` JetStream and filter `marketbot.v1.analysis.result.>`.

## Engine assembly

- `configs/marketbot/<version>.yaml` is the sole operational definition of engine implementation,
  strategy artifact/version, and mode. The default path comes from `MARKETBOT_DEFINITION_PATH`.
- Construct operational engines only through `app.integration.engine_assembly.MarketBotAssembly`.
  Do not add a direct engine constructor or a version-selection map to a composition, worker,
  analyzer, or CLI command.
- Add new implementations to the central catalog, retain prior implementations for rollback, and
  add a new immutable MarketBot definition for a reviewed assembly change.
- Keep implementation version, strategy version, and operational mode distinct in diagnostics.
  Run `uv run marketbot assembly` to inspect the effective selection.
- `MARKETBOT_ENTRY_CONFIRMATION_RULE_VERSION` is deprecated compatibility for atomic rollback of
  Swing/Intraday/Entry Watcher only; do not use it as a second general configuration mechanism.

## Codex ticker-report workflow

- When the user asks Codex for a MarketBot report or analysis of a ticker, run the
  repository analyzer from the repository root. The canonical invocation is
  `uv run market-bot -analyzer TICKER`.
- `TICKER` always comes from the user's request. Never substitute a fixed ticker,
  reuse a ticker from an earlier report, or hardcode a symbol. The analyzer normalizes
  and validates the received value.
- For an explicit per-engine timeout or to disable NATS during diagnostics, use the
  equivalent form `uv run marketbot analyzer TICKER --timeout-seconds SECONDS
  [--no-nats]`. Normal reports should keep NATS enabled so downstream engines can
  consume the current Core results.
- Do not invoke Peter Lynch or the SEC/dilution scan before or after the analyzer.
  They are deliberately excluded from this report mode because their external-provider
  paths are slow. Mention this exclusion in the report when it matters.
- Wait for the analyzer to finish and interpret the final structured report; do not
  treat initialization logs as the result. A failure in one engine is partial
  degradation, not failure of the whole report.
- Interpret engine statuses as follows:
  - `COMPLETED`: analyze the returned result and include its material evidence.
  - `SKIPPED`: the engine was intentionally inapplicable. State the gate/reason; do
    not present it as bearish evidence. Holdings-only engines normally skip tickers
    that are not positive local holdings, and Portfolio Flow requires a live
    quote/trade window.
  - `FAILED`: identify the unavailable engine and error type without inventing a
    verdict from it.
  - `TIMED_OUT`: identify the timeout and continue interpreting completed engines.
- In the Core result, interpret every returned Long, Swing, and Intraday
  `AnalysisResult`: verdict, direction, score, confidence, reasons, risk flags,
  reference price, support/resistance, entry or buy zones, invalidation, targets,
  VWAP/AVWAP gates, regime, and confirmation quality when present. Also report the
  Entry Watcher/Alert availability declared by Core.
- Interpret Market Rotation as global context, not as a ticker-specific vote.
  Patreon Caps, Elliott Wave, Support Confirmation, Long Portfolio, and Signal Fusion
  retain their analytical, holdings-only, or allocation gates. Do not blur
  a counterfactual calculation with an operational buy confirmation.
- State the report's data time and whether the market was regular, premarket, or
  closed when that can be established from the output. Intraday evidence without a
  completed confirmation timeframe must be described as provisional.
- End with one unambiguous maturity conclusion: no buy maturity, or the actual
  L1/L2/L3/L4 alert emitted by MarketBot. Do not promote `WATCH`, proximity to support,
  high relative volume, or a bullish direction field into a confirmed buy when its
  confirmation gates failed.
- Include actionable levels only when the engines returned them, and preserve the
  distinction between entry, invalidation, resistance, and target. Never fabricate
  missing levels or infer a vote from an unavailable engine.
- This workflow is analysis-only. It must not submit orders, enable execution, or
  change engine rules while producing a report.

## Change coordination

- Respect folder ownership during parallel work and do not revert unrelated edits.
- Changes to shared contracts, root dependencies, or CI need root integrator review.
- Prefer additive, backwards-compatible contract evolution. Record irreversible architecture decisions before implementation.
