# Contratos v1

`UniverseChanged` es el snapshot aditivo del universo Core. El coordinador lo publica en
`marketbot.v1.universe.changed.core` desde el refresh central. Los consumidores reciben el reemplazo
completo y los deltas exactos; `consumer_warmup_required=true` les exige cargar su historia antes de
activar simbolos nuevos, sin conocer versiones de implementacion del productor.

`EntrySignal` es la decision analitica de entrada independiente del productor. Los consumidores usan
la familia, la madurez core opcional, los niveles y la procedencia de politica/eventos. Solo
`CORE_ENTRY` y `CORE_RECOVERY` usan L1-L4; Patreon Caps, Long Portfolio, Signal Fusion y Portfolio
Flow conservan familias distintas y no se promocionan artificialmente a L4 core.

`EntrySetupAssessment` transporta evidencia de un setup sin decidir compra ni asignar L1-L4.
Entry Recovery 1.1 publica `CORE_RECOVERY` por este contrato; Alert 3.2 aplica su artefacto de
calidad y solo entonces publica el `EntrySignal` confirmado. Los consumidores no conocen la
implementacion ni la version del productor del assessment.

Este paquete es la frontera de datos estable entre detectores, reglas, estrategias,
alertas y servicios. No contiene ejecución de reglas, registry ni persistencia.

## Garantías

- Todos los modelos heredan de `StrictFrozenModel`: Pydantic v2 en modo estricto,
  instancias congeladas y campos extra prohibidos.
- Las colecciones públicas son tuplas. Los datos dinámicos se expresan como tuplas
  de `NamedValue`/submodelos para evitar diccionarios mutables en los contratos que
  requieren inmutabilidad.
- Todos los timestamps deben ser timezone-aware y tener offset UTC (`+00:00`).
- Precios, scores, cantidades, riesgo, latencias y pesos usan `Decimal`.
- Los hashes tienen la forma exacta `sha256:<64 caracteres hexadecimales minúsculos>`.
- Las versiones de schema, reglas y estrategias usan SemVer.
- `EventEnvelope.event_id` es siempre UUIDv7. `new_uuid7()` funciona también en
  versiones de Python que aún no ofrecen `uuid.uuid7()`.

## Superficie pública

Importar desde `app.contracts`, no desde módulos internos.

- Eventos: `EventEnvelope`, `MarketSession`.
- Reglas: `RuleType`, `RuleStatus`, `RuleLifecycleStatus`, `RuleMetadata`,
  `RuleInputDeclaration`, `RuleOutputDeclaration`, `EvaluationContext`,
  `RuleResult`, `RulePackManifest`.
- Estrategias: `StrategyMode`, `PipelineStep`, `RuleBinding`, `StrategyPolicies`,
  `ScoringPolicy`, `StrategySpec`, `CompiledStrategy`.
- Decisiones: `RuleTraceStep`, `DecisionTrace`. El estado
  `RuleTraceStatus.SKIPPED_DEPENDENCY` vive en la traza, no en `RuleStatus`, porque
  una regla que no se ejecutó no produjo un resultado.
- Mercado y entrega: `PatternCandidate`, `TradePlan`, `AlertPolicy`,
  `AlertDecision`, `AnalysisResult`, `LocalAlert`, `ServiceHealth` y sus submodelos/enums.
  `LocalAlert.component_analyses` es opcional y conserva los resultados completos que explican
  una alerta; `LocalAlert.metrics` agrega contexto propio de la notificacion, como la zona original
  de un Entry Watch, sin expresar una orden.
  `AlertKind.PORTFOLIO_FLOW_BUY` identifica presión compradora agresiva observada al ask; es una
  alerta temprana y no equivale por sí sola a una madurez L1-L4.
- PatreonCaps: `PatreonCapsAssessment` publica cada evaluacion relevante y
  `PatreonCapsTransition` registra un cambio durable de estado. Ambos exponen la version exacta de
  reglas, scores y aportes, niveles, alineacion de engines, snapshot macro, lectura Lesson y
  sizing. `lesson_score`, `lesson_gate_passed`, `lesson_reasons` y `lesson_metrics` permiten
  medir SMA50/200, cruces, triangulo y Wave 1/2 sin perder el calculo que produjo la alerta. Los estados
  publicos viven en `PatreonCapsState`; el regimen macro, en `MacroRegime`.

Los subjects PatreonCaps se construyen con `patreon_caps_assessment_subject()` y
`patreon_caps_transition_subject()`. Las constantes de tipo de evento son
`PATREON_CAPS_ASSESSMENT_EVENT` y `PATREON_CAPS_TRANSITION_EVENT`.

- Elliott Wave: `WaveAssessment` publica una hipotesis observacional independiente con
  `WavePhase`, niveles, alternativa, evidencia y hash del contexto. Usa
  `elliott_wave_assessment_subject()` y `ELLIOTT_WAVE_ASSESSMENT_EVENT`; no ocupa un
  `AnalysisHorizon` y por eso no reemplaza el estado de Long, Swing o Intraday. Los campos
  aditivos `data_as_of` y `assessed_at` separan la vela estructural de la hora real de evaluacion;
  `occurred_at` conserva el valor historico de `data_as_of` por compatibilidad v1.
- Support Confirmation: `SupportAssessment` separa evidencia de reaccion local de evidencia de
  reversion estructural; `SupportTransition` conserva cada cambio de estado. Sus subjects viven en
  `marketbot.v1.support-confirmation.*` y no modifican eventos de PatreonCaps. Comparte la misma
  separacion aditiva `data_as_of`/`assessed_at` que Elliott Wave. `NO_NEARBY_SUPPORT` distingue la
  ausencia de una zona accionable cercana de la ausencia de estructura: `structural_supports`
  conserva referencias higher-timeframe y los campos `impulse_*` describen el ultimo pivote que
  inicio un avance confirmado.

- Signal Fusion combina mensajes existentes sin recalcularlos. `FusionAssessment` separa zona de
  soporte valida (`support_zone_gate`), reaccion/defensa (`support_reaction_gate`) y reversion
  estructural (`support_gate`). `FusionTransition` conserva cambios. El evento
  `signal-fusion.buy-confirmed` exige todos los gates estructurales; el evento aditivo
  `signal-fusion.recovery-confirmed` permite una entrada tactica cuando zona y reaccion de soporte,
  trigger Elliott, confirmacion Intraday, SEC, cartera y beneficio/riesgo pasan, aunque Long y la
  estructura de soporte todavia no hayan confirmado. PatreonCaps es contexto derivado, no un voto
  adicional.

- Options Gamma publica `GammaAssessment` por
  `marketbot.v1.options-gamma.assessment.<SYMBOL>` y una proyeccion compatible como
  `AnalysisResult(OPTIONS_GAMMA)`. El assessment conserva niveles por vencimiento, cobertura,
  calidad, TTL y la hipotesis explicita de signo dealer. Los consumidores usan la proyeccion
  estable y deben ignorarla cuando esta vencida, degradada o distante del spot actual.

- Entry Opportunity: `EntryOpportunity.source_cursors` conserva un cursor causal acotado por
  stream de entrada. Es aditivo y opcional para que los snapshots v1 anteriores se lean con una
  tupla vacia; los consumidores no deben usarlo como version del engine productor.
  `primary_signal_family` y `signal_references` registran setups por familia/politica sin guardar
  identidad ni version del engine productor. Solo referencias core llevan `maturity`; Patreon
  Caps, Long Portfolio, Signal Fusion y Portfolio Flow conservan `maturity=None`.

## Invariantes comprobadas

Cada `PipelineStep` fija `rule_id` y `rule_version` SemVer exactos; bindings y scoring
continúan referenciando `rule_id`. `StrategySpec` rechaza reglas duplicadas y valida
cobertura de bindings, referencias, ciclos y pesos. `CompiledStrategy` compara las
coordenadas exactas `(rule_id, rule_version)` contra el manifest y valida un orden
topológico completo. `validate_primary_uniqueness()` verifica que haya como máximo una
estrategia `PRIMARY` para cada `(family, engine, run_id)`; es una función pura para
que un futuro registry o loader pueda aplicarla sin acoplar persistencia aquí.

`RulePackManifest` permite publicar varias versiones del mismo `rule_id` para A/B,
pero rechaza una coordenada `(rule_id, version)` repetida.

Los planes LONG requieren `stop_loss < entry < take_profits`; los SHORT aplican el
orden inverso. Candidatos, trazas, alertas y salud también validan intervalos,
identificadores duplicados y combinaciones de estado incoherentes.

## Evolución

Los cambios compatibles agregan campos opcionales o nuevos valores sólo con una
revisión explícita de consumidores. Los cambios incompatibles requieren una nueva
versión mayor del contrato/evento. `schema_version` versiona el payload lógico; la
versión del paquete se administra fuera de esta carpeta.

## Ejecución de tests

```powershell
python -m pytest app/contracts/tests -q
```

Los tests son contractuales: además del camino feliz, fijan coerción prohibida,
inmutabilidad, UTC, UUIDv7, hashes, grafos, PRIMARY y restricciones de dominio.
