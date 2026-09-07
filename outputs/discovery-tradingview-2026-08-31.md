# Discovery SwingTrade para TradingView — 2026-08-31

Próxima rueda: **2026-09-01**. Se revisaron **11.120** símbolos elegibles; **54** quedaron en zona y **25** cerca por arriba. Estado: **DEGRADADA_IEX**.

- Cierre diario usado: 2026-08-31
- Ejecución: 2026-08-31 20:00 ET
- Fuente/feed: Alpaca MCP, IEX (SIP no disponible por ventana de demora)
- Definición: MarketBot 7.43.0; SwingTrade 1.4.0; reglas 1.2.0
- Watchlist: PostgreSQL local, 247 símbolos, actualizada 2026-08-25T02:16:18.702637+00:00
- Ajuste por splits: 12 acciones corporativas aplicadas a precios, volumen y VWAP históricos

## Cruce con la watchlist

Nuevos en el top 20: **16**.

PKG,PNC,RF,COO,KBE,PCAR,KRE,IP,MDY,PH,AER,IJH,CFG,FSLY,PINS,VSXY

Ya presentes en el top 20: **JD,KWEB,AXP,HD**.

En el conjunto completo de 79 candidatos hubo 58 símbolos fuera de la watchlist:

PKG,PNC,RF,COO,KBE,PCAR,KRE,IP,MDY,PH,AER,IJH,CFG,FSLY,PINS,VSXY,SYRE,PHM,FCNCA,SYK,CB,CL,WSM,ACGL,BBIO,GGG,AFRM,RY,DOC,ATI,BJ,VTRS,TROW,WST,BBY,GMED,TEL,NVS,AVT,AGNC,XLP,COMP,ZM,ITW,ONB,ABVX,AMH,PFG,P,IRTC,IEX,OC,VSEC,ORLY,SYY,HIG,TREX,IJR

## Top operativo

| # | Ticker | Watchlist | Estado | Proxy | Cierre | Zona F61.8–F50 | Invalida | R/R | AVWAP pivot | AVWAP breakout | Gate |
|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | JD | existente | EN_ZONA_CONFLUENTE | ST2_PROXY | 28.2100 | 27.9632–29.0175 | 27.8564 | 14.9192 | 28.9298 (2026-08-18) | 29.6899 (2026-08-10) | NO |
| 2 | KWEB | existente | EN_ZONA_CONFLUENTE | ST2_PROXY | 25.7800 | 25.4888–26.1850 | 25.5530 | 14.7821 | 26.6265 (2026-08-13) | 26.9693 (2026-08-10) | NO |
| 3 | PKG | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 233.9200 | 231.2033–236.4325 | 231.9607 | 12.4688 | 251.0849 (2026-07-31) | 250.7800 (2026-07-24) | NO |
| 4 | PNC | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 239.6300 | 238.7361–242.5900 | 237.4236 | 8.7426 | 243.4283 (2026-08-20) | 0 | NO |
| 5 | RF | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 29.8900 | 29.7693–30.2850 | 29.6016 | 8.2873 | 30.4944 (2026-08-21) | 31.2100 (2026-07-16) | NO |
| 6 | COO | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 70.1000 | 69.9389–71.4800 | 69.1173 | 8.0494 | 73.5060 (2026-08-17) | 74.4477 (2026-08-11) | NO |
| 7 | KBE | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 67.8050 | 66.8911–67.8475 | 67.2934 | 8.0042 | 68.2277 (2026-08-27) | 69.7555 (2026-07-16) | NO |
| 8 | PCAR | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 124.0950 | 123.6002–126.5750 | 122.5170 | 7.8959 | 128.4939 (2026-08-20) | 131.8716 (2026-07-28) | NO |
| 9 | KRE | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 73.5800 | 73.0846–74.0900 | 72.9712 | 7.8357 | 75.5804 (2026-08-10) | 75.9891 (2026-07-16) | NO |
| 10 | AXP | existente | EN_ZONA_CONFLUENTE | ST2_PROXY | 330.2900 | 329.2564–335.7175 | 327.3550 | 7.3322 | 334.7893 (2026-08-20) | 0 | NO |
| 11 | IP | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 37.9500 | 36.7575–38.2950 | 37.2496 | 7.0250 | 40.5307 (2026-08-17) | 41.5786 (2026-07-28) | NO |
| 12 | MDY | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 687.1600 | 686.5405–692.3850 | 682.8896 | 7.0228 | 693.9372 (2026-08-25) | 703.0932 (2026-08-04) | NO |
| 13 | PH | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 981.3700 | 958.6499–985.2400 | 964.2321 | 6.8002 | 1009.4500 (2026-08-21) | 1043.0714 (2026-08-06) | NO |
| 14 | AER | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 145.9200 | 143.0553–146.0625 | 144.0164 | 6.7689 | 147.3201 (2026-08-21) | 0 | NO |
| 15 | IJH | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 75.3650 | 75.2354–75.8950 | 74.8671 | 6.6786 | 76.0157 (2026-08-25) | 77.2203 (2026-08-04) | NO |
| 16 | CFG | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 68.8900 | 68.0701–69.4525 | 67.7629 | 5.6958 | 69.8346 (2026-08-21) | 72.1464 (2026-07-16) | NO |
| 17 | FSLY | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 22.7850 | 21.4280–23.1850 | 21.3880 | 5.6157 | 23.5090 (2026-08-25) | 26.1008 (2026-08-11) | NO |
| 18 | PINS | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 21.7000 | 21.4342–22.2525 | 20.9755 | 5.5489 | 22.6833 (2026-08-20) | 23.4191 (2026-08-04) | NO |
| 19 | HD | existente | EN_ZONA_CONFLUENTE | ST2_PROXY | 327.9400 | 326.8568–332.8825 | 322.1396 | 5.2342 | 336.5646 (2026-08-18) | 340.1673 (2026-08-07) | NO |
| 20 | VSXY | NUEVO | EN_ZONA_CONFLUENTE | ST2_PROXY | 85.4100 | 83.9212–87.4400 | 81.8770 | 4.7947 | 88.2526 (2026-08-25) | 89.8989 (2026-08-10) | NO |

Los AVWAP están rotulados **AVWAP_CALCULADO_MCP** y son contexto complementario, no campos publicados por SwingTrade. Un `0` indica que no existe ancla confirmada dentro de las últimas 60 ruedas.

## Cobertura

- Activos activos devueltos: 14.275
- Tradables en NASDAQ/NYSE/AMEX/ARCA/BATS: 13.096
- Elegibles tras excluir OTC, warrants, rights, units y formatos no operativos: 11.120
- Con al menos 20 ruedas: 10.787
- Excluidos por cierre < USD 3: 1.133
- Excluidos por ADV20 IEX < USD 5 M: 8.432
- Sin 20 ruedas completas: 333
- Pasaron precio/liquidez: 1.222
- Con 60 ruedas: 1.217; estructuras LONG causales calculables: 763
- Seleccionados: 79 (30 EN_ZONA_CONFLUENTE, 24 EN_ZONA, 25 CERCA_SUPERIOR)
- Productos apalancados/inversos seleccionados: 0
- Lotes fallidos tras reintento: 0

## Lista compacta

JD,KWEB,PKG,PNC,RF,COO,KBE,PCAR,KRE,AXP,IP,MDY,PH,AER,IJH,CFG,FSLY,PINS,HD,VSXY

El CSV para TradingView está en `outputs/discovery-tradingview-2026-08-31.csv`.

Discovery estructural para monitoreo; no es señal de compra, ST3/ST4 ni autorización de ejecución.

