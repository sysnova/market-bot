# Patreon Learning Centre: reglas candidatas para Market Bot

Fecha de revisión: 2026-08-01
Fuente: colección privada `The Learning Centre` de The Long Investor, leída mediante
la sesión local autenticada del usuario. Este documento resume las ideas; no reproduce
las publicaciones.

## Alcance

La colección informa 55 publicaciones y aplica el filtro `LESSON`. El filtro devolvió
20 lecciones relevantes. Se revisaron sus textos, transcripciones disponibles y las
imágenes centrales de las publicaciones visuales. Dos videos (`Support Confirmation`
y `Building a Chart`) no expusieron transcripción completa; sus descripciones sí fueron
incluidas.

## Doctrina común extraída

1. Una zona no es una entrada. Primero se identifica soporte y luego se exige evidencia
   de que compradores lo defendieron.
2. El soporte es más confiable cuando coinciden varios niveles: mínimos previos,
   números redondos, medias móviles, Fibonacci, AVWAP y zonas de ruptura/retest.
3. Una reversión sólida debe mostrar cinco ondas o, de forma objetiva, una secuencia
   de máximos y mínimos crecientes. Un rebote de tres tramos puede ser solamente una
   onda B antes de una nueva caída.
4. El análisis técnico y el fundamental no necesitan dar el mismo precio objetivo,
   pero sí deben contar la misma historia direccional.
5. El tamaño debe adaptarse al riesgo, volatilidad, convicción y posición existente.
   La construcción propuesta es escalonada y reserva capital para retests o eventos.
6. VIX, DXY, tasas y bonos cambian el contexto. No deberían sustituir la señal del
   ticker, pero sí subir o bajar el umbral de confirmación.
7. Elliott Wave es probabilístico y contextual. Las imperfecciones son comunes; por
   eso no debe convertirse directamente en una regla binaria de compra.

## Reglas técnicas programables

### 1. Support Confluence Score

Crear un motor puro y versionado que agrupe niveles próximos usando una distancia
normalizada por ATR. Fuentes candidatas:

- mínimos pivot confirmados y soportes históricos;
- SMA 50 y SMA 200 diaria;
- SMA 10, 30, 50 y 200 semanal;
- AVWAP desde pivot low y desde breakout;
- nivel de ruptura previo convertido en soporte;
- retrocesos Fibonacci del impulso confirmado;
- números redondos relevantes.

Dos niveles pertenecen al mismo clúster cuando su distancia es, por ejemplo, menor a
`0.35 ATR`. El resultado debe informar nivel central, ancho, cantidad de fuentes,
antigüedad, número de defensas y `confluence_score`. No debe ocultar las fuentes.

### 2. Support Confirmation State Machine

Estados propuestos:

`WATCH_ZONE -> SUPPORT_TEST -> CONFIRMED_V | CONFIRMED_BASE -> IMPULSE_RETEST -> INVALIDATED`

Confirmación V:

- caída rápida hacia el clúster;
- rechazo intradiario y cierre recuperando el nivel;
- RVOL alto;
- vela de reversión o reclaim de VWAP;
- no atravesar la invalidación ajustada por ATR.

Confirmación por base:

- dos o más defensas en sesiones distintas;
- mínimos crecientes;
- compresión de ATR/volumen durante la base;
- ruptura del máximo de la base con expansión de volumen;
- retest exitoso opcional.

Esto extiende la persistencia de Entry Watcher V3: V3 comprueba continuidad intradía,
pero todavía no mide la calidad del soporte ni diferencia una recuperación en V de una
base construida durante varias sesiones.

### 2.1. Consolidacion SMA50/200, triangulo y Wave 2

La regla activa `patreon_caps@1.1.0` incorpora la lectura adicional de la leccion de
medias moviles y formaciones:

- posicion del cierre, pendiente y relacion de SMA50/200 diaria y de 1 hora;
- Golden Cross y Death Cross diarios dentro de una ventana versionada;
- triangulo ascendente mediante resistencia plana, minimos pivot ascendentes, breakout
  con RVOL y retest;
- Wave 1 objetiva mediante impulso confirmado y Wave 2 defendiendo Fibonacci 0.618;
- retest del maximo de Wave 1 como evidencia adicional de continuacion.

La zona puede seguir en `WATCH_ZONE` o `SUPPORT_TEST`, pero la compra queda bloqueada
si el cierre diario esta bajo SMA200 o existe un Death Cross reciente. Estas reglas
aportan un `lesson_score` separado para comparar `1.1.0` contra `1.0.0` sin reescribir
el historial.

### 3. Fibonacci sobre impulsos confirmados

No calcular Fibonacci desde pivots arbitrarios. Primero debe existir un tramo impulsivo
detectado y persistido. Rangos de referencia extraídos:

- Wave 2: `0.50-0.786`, con `0.618` como nivel típico;
- Wave 3: objetivo típico `1.618`, con extensiones en tendencias fuertes;
- Wave 4: `0.236-0.50`, con `0.382` como nivel típico;
- Wave 5: zona orientativa `2.0-2.618` del esquema de extensión;
- Wave C: suele volver a una profundidad semejante a Wave 2.

Estos niveles suman confluencia; nunca deben confirmar por sí solos.

### 4. Impulse/Correction Hypothesis

Implementar primero en shadow mode mediante pivots ATR/ZigZag:

- impulso candidato: cinco tramos, máximos y mínimos crecientes;
- corrección candidata: tres tramos A-B-C;
- Wave 2 no rompe el origen de Wave 1;
- Wave 3 no puede ser la más corta;
- Wave 4 normalmente no invade Wave 1;
- excepción explícita y penalizada para leading/ending diagonals;
- rebote de tres tramos después de una caída mantiene riesgo de Wave B;
- rebote de cinco tramos aumenta probabilidad de cambio de tendencia.

El resultado debe ser una lista de hipótesis con confianza y violaciones, no una única
etiqueta presentada como certeza.

### 5. Fundamental/Technical Alignment

Stock Analyzer ya calcula crecimiento, márgenes, FCF, caja/deuda y dilución mediante
SEC CompanyFacts. Market Bot todavía no consume ese resultado como contrato.

Agregar un evento versionado con:

- dirección fundamental: improving, stable o deteriorating;
- score de calidad y moat;
- crecimiento de ingresos y EPS;
- margen operativo y FCF;
- caja/deuda y dilución;
- valuación relativa al sector y a su propia historia;
- fecha del último período fiscal.

Reglas sugeridas:

- técnico bullish + fundamental improving: habilita tamaño normal;
- técnico bullish + fundamental stable: tamaño reducido o confirmación adicional;
- técnico bullish + fundamental deteriorating: watch, nunca HIGH CONVICTION;
- técnico bearish + valuación atractiva: value watch, no compra automática;
- datos fundamentales vencidos: flag explícito, no asumir neutralidad.

Los umbrales genéricos de las publicaciones —por ejemplo P/E menor a 15 o current
ratio 2— deben normalizarse por sector. No son gates universales.

### 6. Market Regime Overlay

Incorporar series de DXY, VIX, US10Y y TLT como contexto diario:

- DXY: posición y pendiente frente a SMA 50/200, velocidad del movimiento;
- VIX: percentil, estructura, ruptura y retest; evitar niveles fijos históricos;
- US10Y/TLT: dirección, divergencia y shock de tasas;
- breadth de índices para distinguir riesgo específico de risk-off general.

El overlay modifica thresholds y tamaño, no crea compras por sí solo. Un desplome
rápido del DXY también debe considerarse incertidumbre, aunque un dólar débil gradual
pueda favorecer multinacionales y commodities.

### 7. Position Builder

Reemplazar la primera tranche fija de 50% por una política versionada configurable.
La lección propone cinco etapas:

1. ruptura inicial y soporte de Wave 1;
2. retest/Wave 2;
3. avance confirmado en Wave 3;
4. subwave 2 dentro de Wave 3;
5. reserva para shock o retest extraordinario.

Para automatización, cada tranche debe quedar limitada por:

- faltante contra el target del portfolio;
- riesgo máximo en USD dividido por distancia a invalidación;
- volatilidad/ATR;
- riesgo sectorial agregado;
- proximidad a earnings o evento binario;
- holdings actuales consultados en PostgreSQL.

La recomendación de no usar stops no debe trasladarse al bot. Market Bot debe conservar
invalidation estructural y distinguir entre alerta técnica, reducción de tamaño y salida.

## Qué ya existe y qué falta

| Capacidad | Estado actual | Mejora propuesta |
|---|---|---|
| SMA 20/50/150/200 diaria y medias semanales | Existe | Exponer clúster y pendiente como confluencia |
| Soporte/resistencia | Existe, lookback simple | Pivots, múltiples defensas y score de confluencia |
| RSI y RVOL | Existe | Divergencia y contexto de reacción en soporte |
| AVWAP de pivot/breakout | Existe en Swing | Integrarlo al clúster LONG |
| Confirmación persistente | Existe en V3 | Estados V/base/retest multisesión |
| Fibonacci | No existe | Añadir sólo sobre impulso confirmado |
| Elliott Wave | No existe | Shadow hypothesis, nunca gate inicial |
| Fundamentals | Existe en Stock Analyzer | Contrato hacia Market Bot y alineación direccional |
| VIX/DXY/US10Y/TLT | No existe en Market Bot | Overlay de régimen versionado |
| Tamaño por holdings/target | Parcialmente implementado | Riesgo por invalidación y cinco tranches |
| Riesgo de earnings/news | SEC/dilución parcial | Event-risk gate y cooldown post-shock |

## Orden recomendado de implementación

### Fase 1 — mayor impacto y menor subjetividad

1. `support_confluence@1.0.0` en shadow mode.
2. `support_confirmation@1.0.0` con confirmación V y base.
3. `position_builder@2.0.0` con riesgo por invalidación y cinco tranches.
4. Replays sobre las alertas históricas existentes.

### Fase 2 — calidad de tesis y contexto

1. Contrato `FundamentalSnapshot@1.0.0` desde Stock Analyzer.
2. `fundamental_technical_alignment@1.0.0`.
3. `market_regime_overlay@1.0.0` para VIX/DXY/US10Y/TLT.

### Fase 3 — Elliott Wave medible

1. `wave_hypothesis@0.1.0` sólo shadow/research.
2. Persistir hipótesis alternativas y violaciones.
3. Promover a señal auxiliar únicamente si mejora resultados fuera de muestra.

## Métricas para decidir promoción o rollback

- precision de confirmación a 5, 20 y 60 sesiones;
- MFE y MAE desde entrada;
- porcentaje de invalidaciones antes del primer objetivo;
- tasa de falsas rupturas y de retests exitosos;
- retorno y drawdown por tipo de confirmación: V, base, breakout/retest;
- resultados separados por régimen macro y riesgo de evento;
- mejora incremental de cada regla frente a V3, no sólo performance absoluta;
- cobertura: porcentaje de símbolos donde los datos requeridos estaban disponibles.

## Lecciones revisadas

1. [$NVO - Technicals x Fundamentals Video - Lesson](https://www.patreon.com/posts/158318691)
2. [Elliot Wave Theory - Lesson I.](https://www.patreon.com/posts/161959521)
3. [Support Confirmation - Video - Lesson](https://www.patreon.com/posts/156336766)
4. [LESSON - REGULAR FLAT CORRECTION](https://www.patreon.com/posts/126706867)
5. [LESSON - $DXY - IMPACT ON THE MARKET](https://www.patreon.com/posts/124272467)
6. [LESSON - EWT CHEAT SHEET](https://www.patreon.com/posts/116948507)
7. [LESSON - FUNDAMENTAL ANALYSIS - RULE OF THUMBS](https://www.patreon.com/posts/113873053)
8. [$NIO TOP 20 - LESSON - WAVE 4 VARIATIONS](https://www.patreon.com/posts/112040218)
9. [LESSON - FINDING SUPPORT CONFIRMATION](https://www.patreon.com/posts/111467758)
10. [LESSON - FIBONACCI LEVELS AND ELLIOTT WAVE](https://www.patreon.com/posts/109839135)
11. [$VIX - LESSON](https://www.patreon.com/posts/108784372)
12. [LESSON - POSITION SIZING](https://www.patreon.com/posts/108728175)
13. [LESSON - BUILDING A CHART - $AFRM](https://www.patreon.com/posts/108167000)
14. [$HOOD - PERFECT IMPULSE WAVE - LESSON](https://www.patreon.com/posts/106365000)
15. [LESSON - FINDING FUNDAMENTALLY UNDERVALUED POSITIONS](https://www.patreon.com/posts/104724191)
16. [LESSON - TECHNICAL ANALYSIS THEORY - $DAL](https://www.patreon.com/posts/104246194)
17. [$DG - LEADING DIAGONAL - LESSON CASE STUDY](https://www.patreon.com/posts/103327366)
18. [LESSON - MOVING AVERAGES - $MRNA Case](https://www.patreon.com/posts/102839249)
19. [Lesson - Identifying a Down-Trend](https://www.patreon.com/posts/102782856)
20. [LESSON: US 10Y and the $TLT Relationship](https://www.patreon.com/posts/102410645)

## Conclusión

El aporte más fuerte de las lecciones no es Elliott Wave aislado. Es el proceso:

`calidad fundamental -> tendencia -> zona con confluencia -> defensa del soporte -> impulso/retest -> tamaño escalonado`

Market Bot hoy es fuerte en tendencia y timing inmediato, pero débil en confluencia,
contexto fundamental compartido y construcción progresiva de posición. Ese es el orden
correcto para fortalecerlo sin convertir interpretaciones visuales en certeza automática.
