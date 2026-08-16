# Foundation integration

This package is the repository-owned composition boundary. It may import concrete
engines to prove the whole milestone, while individual engines remain coupled only
through contracts and ports.

`prepare_foundation_engine` discovers the trusted synthetic rule pack, freezes one
PAPER registry snapshot, compiles PRIMARY and CANDIDATE strategies, executes them on
the same context, and durably audits traces and decisions as idempotent NDJSON.

`distributed_composition.py` is the active process boundary for the analytical MVP. It creates one
private engine per process, performs horizon-specific REST bootstrap, subscribes that process to
logical durable NATS subjects, and publishes only stable contracts. The Alpaca WebSocket,
persistent Entry Watcher, Entry Opportunity, Alert, outbox relay, and Entry Recovery have separate
composition roots. Engine implementations are selected independently by the immutable MarketBot
definition; consumers never route on a producer implementation version.

The legacy live composition also connects the pure horizon engines to `EntryWatcher`. Analysis results
remain the only cross-engine input. The watcher persists through the PostgreSQL adapter, while
`AnalysisRuntime` converts its lifecycle transitions into local human alerts. Failure to connect
to the optional watcher database is isolated from Alpaca analysis and NATS mirroring.

The active distributed analytical generation is selected by `configs/marketbot/7.10.0.yaml`:
Long 2, Swing 5, Intraday 4, Entry Watcher 5.4, Entry Opportunity 3.0, Alert 3.5,
Entry Recovery 1.1, Volume Structure 1.1, Options Gamma 1.0, and Signal Fusion 0.5.
Patreon Caps, Elliott Wave, and Support Confirmation remain available on demand but are excluded
from the automatic runtime while startup memory is being measured.
Earlier classes and definitions remain available for replay and rollback; changing an active
generation must never silently rewrite an old class or versioned rule artifact.

`patreon_caps_composition.py` agrega el analisis PatreonCaps v1 sin consumir alertas
humanas deduplicadas. Hace bootstrap REST de 260 ruedas diarias, 220 semanales, 220 barras de 1H
y 160 barras de 15 minutos; luego une barras finalizadas de NATS con los `AnalysisResult` completos de Long,
Swing V3 e Intraday V3. El universo y los targets `PORT_YTD` se leen exclusivamente del PostgreSQL
local. Cada transicion se confirma primero en PostgreSQL y recien despues se publica por NATS.

Los monitores dedicados viven en `patreon_caps_monitor.py`: la vista de analisis consume
assessments vivos y la vista de alertas precarga las ultimas 50 transiciones persistidas antes de
continuar por NATS.

`entry_opportunity_monitor.py` materializa una vista terminal de todas las oportunidades activas y
del historial reciente. PostgreSQL provee el snapshot durable; los eventos NATS fuerzan el refresco
inmediato y un polling acotado incorpora marcas de mercado que no generan eventos materiales.
