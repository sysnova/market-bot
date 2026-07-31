# Replay contrafactual Entry Confirmation V3 - 2026-07-31

Replay sin look-ahead aplicado a las alertas V2 persistidas en JetStream. Para cada hora original
se usaron solamente las barras de un minuto que ya estaban cerradas, los componentes Long/Swing
incluidos en el evento y las compuertas declaradas por la regla `3.0.0`.

## Resumen

| Medida | V2 observado | V3 contrafactual |
|---|---:|---:|
| Eventos evaluados | 174 | 55 |
| Simbolos con senal | 48 | 29 |
| ENTRY_CONFIRMED | 120 | 48 |
| HIGH_CONVICTION_BUY | 22 | 4 |
| ENTRY_WATCH confirmado | 32 | 3 |
| Eventos filtrados | - | 119 (68.4%) |
| Eventos transformados a otra categoria | - | 6 |

## Primera senal V3 por simbolo

Horas en Argentina (UTC-3). Los stops y objetivos fueron recalculados con el piso V3 basado en
precio y ATR.

| Hora AR | Ticker | Senal inicial V3 | Entrada | Stop V3 | Objetivo V3 |
|---|---|---|---:|---:|---:|
| 09:56 | ADBE | ENTRY_CONFIRMED | 244.5000 | 243.8888 | 245.4169 |
| 10:28 | SBUX | ENTRY_CONFIRMED | 106.3000 | 106.0343 | 106.6986 |
| 10:29 | ANET | ENTRY_CONFIRMED | 177.3969 | 176.9534 | 178.0621 |
| 10:31 | CIFR | ENTRY_CONFIRMED | 24.3350 | 24.1612 | 24.5957 |
| 10:32 | BMY | ENTRY_CONFIRMED | 65.4300 | 65.2269 | 65.7347 |
| 10:55 | INTU | ENTRY_CONFIRMED | 316.0850 | 315.1699 | 317.4577 |
| 11:32 | AMZN | ENTRY_CONFIRMED | 271.2300 | 270.2200 | 272.7450 |
| 12:05 | HWM | ENTRY_CONFIRMED | 282.4950 | 281.7888 | 283.5544 |
| 12:17 | CTRE | ENTRY_CONFIRMED | 41.6600 | 41.5559 | 41.8162 |
| 12:25 | CVX | HIGH_CONVICTION_BUY | 194.8700 | 194.3828 | 195.6008 |
| 12:27 | MRK | ENTRY_CONFIRMED | 130.7300 | 130.4032 | 131.2202 |
| 12:28 | RTX | ENTRY_CONFIRMED | 213.9800 | 213.4451 | 214.7824 |
| 12:52 | REGN | ENTRY_CONFIRMED | 742.2500 | 740.3944 | 745.0334 |
| 13:02 | MMM | ENTRY_CONFIRMED | 176.4200 | 175.9790 | 177.0816 |
| 13:32 | ETN | ENTRY_CONFIRMED | 411.6500 | 410.6209 | 413.1937 |
| 13:37 | DE | ENTRY_CONFIRMED | 596.9150 | 595.4227 | 599.1534 |
| 13:41 | OXY | ENTRY_CONFIRMED + ENTRY_WATCH | 56.3005 | 56.1597 | 56.5116 |
| 13:51 | VRTX | ENTRY_CONFIRMED | 475.3600 | 474.1716 | 477.1426 |
| 14:17 | DASH | ENTRY_CONFIRMED | 196.4500 | 195.9589 | 197.1867 |
| 14:36 | APH | ENTRY_CONFIRMED | 162.3100 | 161.9042 | 162.9187 |
| 15:03 | PSX | ENTRY_CONFIRMED | 211.1200 | 210.5922 | 211.9117 |
| 15:03 | VLO | ENTRY_CONFIRMED | 309.2750 | 308.5018 | 310.4348 |
| 15:07 | PLD | ENTRY_CONFIRMED | 144.9300 | 144.5677 | 145.4735 |
| 15:13 | TMO | ENTRY_CONFIRMED | 574.1400 | 572.7047 | 576.2930 |
| 15:27 | LMT | ENTRY_CONFIRMED | 580.7390 | 579.2872 | 582.9168 |
| 15:50 | ZETA | ENTRY_CONFIRMED | 21.6400 | 21.5859 | 21.7212 |
| 16:22 | MPC | ENTRY_CONFIRMED | 316.0900 | 315.2998 | 317.2753 |
| 16:45 | SCHW | ENTRY_CONFIRMED | 105.6250 | 105.3609 | 106.0211 |
| 16:49 | XOM | ENTRY_CONFIRMED | 156.0800 | 155.6898 | 156.6653 |

## Casos analizados anteriormente

- **HRL:** queda completamente filtrada por falta de persistencia intradia y por tener ambos AVWAP
  Swing por encima del precio.
- **CTRE:** pierde HIGH_CONVICTION y todas las confirmaciones de Entry Watcher porque no recupera
  los AVWAP Swing. Conserva ENTRY_CONFIRMED por la combinacion Long + Intraday.
- **PLD:** la primera confirmacion queda filtrada. A las 15:07 AR conserva ENTRY_CONFIRMED por Long
  + Intraday, pero pierde HIGH_CONVICTION y Entry Watcher por la compuerta Swing.

## Limitacion metodologica

Este es un replay contrafactual sobre los snapshots de analisis emitidos por V2, enriquecidos con
las barras historicas necesarias para la persistencia V3. No recompone desde cero cada indicador
Long y Swing, pero reproduce las decisiones adicionales introducidas por V3 sin utilizar barras
posteriores a cada alerta.
