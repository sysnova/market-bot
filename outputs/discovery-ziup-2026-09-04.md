# Discovery ZIUP_DISCOVERY_V1 — cierre 2026-09-04

Próxima rueda: **2026-09-08**. Se revisaron **4242 de 4296** símbolos que superaron precio/liquidez e historial inicial; **36** son CANDIDATO y **83** CANDIDATO_SIN_VOLUMEN. Estado: **COMPLETA (SIP)**.

- Ejecución: 2026-09-05, mercado cerrado.
- Fuente: Alpaca MCP, feed SIP, barras 1Day completas ajustadas por split.
- Definición: MarketBot 7.49.0; SwingTrade 1.6.0; reglas SwingTrade 1.3.0.
- Selección: ZIUP_DISCOVERY_V1, R/R estricto > 1.50, ADV20_USD >= 5,000,000.
- Exportación operativa: Pine MarketBot v3.3, schemaVersion 3, exactamente 81 columnas.
- Enriquecidos: 116 regulares y 3 productos apalancados/inversos; no se eliminó ningún candidato por gates MarketBot.

## Top operativo Ziup

| # | Ticker | Estado | Cierre | Zona Ziup F61.8–F50 | Soporte | Resistencia | Target | R/R | RVOL20 | ADV20 USD |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | GFR | CANDIDATO | 6.1600 | 6.0803–6.2750 | 6.0600 | 7.1000 | 7.1000 | 50.2315 | 1.0602 | $5,291,126 |
| 2 | COO | CANDIDATO | 69.5900 | 69.5461–70.9350 | 69.2800 | 76.8200 | 76.8200 | 27.3310 | 1.2019 | $121,551,990 |
| 3 | CB | CANDIDATO | 341.5900 | 338.7182–343.4500 | 338.3100 | 348.3000 | 348.3000 | 23.4733 | 1.4906 | $570,026,622 |
| 4 | HMN | CANDIDATO | 50.9400 | 50.5232–51.2100 | 50.3900 | 52.8400 | 52.8400 | 17.3879 | 1.1992 | $11,804,307 |
| 5 | PBA | CANDIDATO | 48.4100 | 47.9339–48.5900 | 47.8400 | 49.3600 | 49.3600 | 15.1840 | 1.7517 | $40,679,425 |
| 6 | VBK | CANDIDATO | 349.4400 | 346.4022–350.1900 | 344.6300 | 366.2400 | 366.2400 | 11.1939 | 1.5402 | $60,140,097 |
| 7 | OC | CANDIDATO | 138.7200 | 135.7541–140.1850 | 133.6800 | 158.9200 | 158.9200 | 11.1691 | 1.7346 | $120,914,106 |
| 8 | FDX | CANDIDATO | 322.5200 | 318.8757–322.7850 | 316.5400 | 339.3500 | 339.3500 | 8.7660 | 1.2871 | $515,272,223 |
| 9 | TTC | CANDIDATO | 93.3300 | 93.2368–94.5950 | 92.3700 | 100.3500 | 100.3500 | 8.2061 | 1.9341 | $73,888,218 |
| 10 | TMUS | CANDIDATO | 181.5200 | 178.3916–181.6850 | 177.1300 | 188.0200 | 188.0200 | 7.6318 | 1.1778 | $693,308,200 |
| 11 | ETN | CANDIDATO | 410.8500 | 399.3466–410.9200 | 390.7100 | 459.9600 | 459.9600 | 7.0182 | 1.1074 | $725,505,425 |
| 12 | PFBC | CANDIDATO | 103.5200 | 102.6035–103.9050 | 101.7400 | 107.9000 | 107.9000 | 6.1341 | 1.4601 | $8,128,105 |
| 13 | ENR | CANDIDATO | 21.2300 | 20.9840–21.3250 | 20.6500 | 22.7700 | 22.7700 | 5.3477 | 1.0425 | $20,630,376 |
| 14 | FBK | CANDIDATO | 56.9900 | 56.6231–57.6650 | 55.5100 | 62.0800 | 62.0800 | 4.9026 | 1.1047 | $16,050,800 |
| 15 | FLRT | CANDIDATO | 46.7500 | 46.7143–46.7650 | 46.6600 | 46.9800 | 46.9800 | 4.8975 | 1.2440 | $6,894,273 |
| 16 | BRK.A | CANDIDATO | 759350.0100 | 752264.4682–760198.1050 | 743500.0000 | 793815.2100 | 793815.2100 | 4.7408 | 1.0559 | $162,671,695 |
| 17 | SHOO | CANDIDATO | 43.6800 | 42.8591–44.0450 | 41.4400 | 48.8800 | 48.8800 | 4.2428 | 1.3284 | $38,582,533 |
| 18 | FWRG | CANDIDATO | 12.0500 | 11.9866–12.2450 | 11.7000 | 13.1700 | 13.1700 | 4.1295 | 1.0756 | $11,996,380 |
| 19 | IJR | CANDIDATO | 145.3400 | 144.4021–145.5550 | 142.9200 | 150.4400 | 150.4400 | 4.0737 | 1.0279 | $347,616,487 |
| 20 | FIS | CANDIDATO | 41.8900 | 41.1924–42.2650 | 40.4700 | 44.1300 | 44.1300 | 4.0666 | 1.2841 | $216,876,730 |

## Enriquecimiento MarketBot del top

| Ticker | Geometría MB | Zona MB F61.8–F50 | Soporte MB | Invalida MB | Resistencia MB | F161.8 MB | R/R MB | AVWAP pivot | AVWAP breakout | Gate Swing |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| GFR | ENGINE_ASSESSMENT | 6.0291–6.2450 | 5.8400 | 5.7388 | 7.1600 | 8.2909 | 2.3742 | 6.1531 | 6.3201 | false |
| COO | ENGINE_ASSESSMENT | 69.9690–71.5225 | 69.0750 | 68.3199 | 78.1050 | 86.2410 | 6.7042 | 69.8821 | 73.8039 | false |
| CB | ENGINE_ASSESSMENT | 337.3584–342.8100 | 336.6400 | 333.8267 | 351.2500 | 394.4616 | 1.2443 | 342.7642 | 348.2699 | false |
| HMN | ENGINE_ASSESSMENT | 50.6778–51.6100 | 49.9405 | 49.4018 | 53.2450 | 60.4422 | 1.4985 | 51.2041 | 51.7083 | false |
| PBA | ENGINE_ASSESSMENT | 47.6681–48.4150 | 47.4000 | 46.9650 | 49.8100 | 55.4919 | 0.9689 | 48.5244 | 49.0779 | false |
| VBK | ENGINE_ASSESSMENT | 346.1522–350.1401 | 343.9000 | 341.7187 | 367.0379 | 387.9236 | 2.2791 | 347.0143 | 355.7096 | false |
| OC | ENGINE_ASSESSMENT | 132.1433–137.4450 | 132.5850 | 130.3613 | 159.8760 | 187.6767 | 2.5310 | 140.7814 | 146.8828 | false |
| FDX | ENGINE_REJECTED | 0.0000–0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | -1.0000 | 326.6329 | 328.8648 | false |
| TTC | ENGINE_ASSESSMENT | 93.2225–94.6693 | 91.0250 | 89.6904 | 100.8000 | 108.3775 | 2.0524 | 97.1001 | 97.4611 | false |
| TMUS | ENGINE_ASSESSMENT | 178.3405–182.2575 | 173.0950 | 171.0470 | 189.8700 | 219.3695 | 0.7973 | 183.0047 | 181.2082 | false |
| ETN | ENGINE_ASSESSMENT | 403.9173–418.0625 | 384.7701 | 378.0235 | 478.0000 | 552.0828 | 2.0456 | 404.8149 | 422.0337 | false |
| PFBC | ENGINE_ASSESSMENT | 103.2743–104.9900 | 101.0600 | 99.9760 | 108.5250 | 121.2457 | 1.4122 | 103.4704 | 105.4306 | false |
| ENR | ENGINE_REJECTED | 0.0000–0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | -1.0000 | 20.9588 | 21.8812 | false |
| FBK | ENGINE_ASSESSMENT | 56.6079–57.7625 | 55.3000 | 54.7291 | 62.6550 | 68.7021 | 2.5056 | 56.5388 | 59.3043 | false |
| FLRT | ENGINE_ASSESSMENT | 46.7057–46.7600 | 46.6400 | 46.5970 | 46.9900 | 47.2743 | 1.5686 | 46.7079 | 0.0000 | false |
| BRK.A | ENGINE_ASSESSMENT | 753960.4134–763916.4050 | 743500.0000 | 739828.0779 | 806102.8100 | 858245.2066 | 2.3949 | 758897.8391 | 761622.2219 | false |
| SHOO | ENGINE_ASSESSMENT | 42.6301–43.9800 | 41.4000 | 40.7670 | 49.3350 | 56.7699 | 1.9413 | 42.3571 | 46.1841 | false |
| FWRG | ENGINE_ASSESSMENT | 11.8849–12.2200 | 11.5300 | 11.2521 | 13.4000 | 15.3951 | 1.6919 | 12.1866 | 12.5469 | false |
| IJR | ENGINE_ASSESSMENT | 143.7653–145.0875 | 142.6500 | 141.9133 | 150.5000 | 157.6147 | 1.5058 | 144.2135 | 147.2385 | false |
| FIS | ENGINE_ASSESSMENT | 41.0777–42.2075 | 40.0850 | 39.5088 | 44.3900 | 52.9124 | 1.0499 | 41.6651 | 42.5734 | false |

Nota: el CSV v3 no expone por separado el gate AVWAP; se conserva el gate Swing emitido y los dos AVWAP canónicos sin inferir un gate adicional.

## Productos apalancados/inversos separados

| Ticker | Nombre | Estado | Cierre | R/R Ziup | RVOL20 |
|---|---|---|---:|---:|---:|
| TSLS | Direxion Shares ETF Trust Direxion Daily TSLA Bear 1X ETF | CANDIDATO | 57.8700 | 1.6891 | 1.7058 |
| BABX | GraniteShares ETF Trust GraniteShares 2x Long BABA Daily ETF | CANDIDATO_SIN_VOLUMEN | 18.2300 | 22.1830 | 0.6624 |
| TUSI | Touchstone Ultra Short Income ETF | CANDIDATO_SIN_VOLUMEN | 25.3100 | 2.7838 | 0.7071 |

## Cobertura y exclusiones

- Activos activos devueltos por Alpaca: 14.277.
- Tradables en NASDAQ/NYSE/AMEX/ARCA/BATS: 13.094.
- Warrants, rights y units excluidos por tipo: 906.
- Universo elegible: 12.188.
- Con al menos 20 ruedas: 12043.
- Excluidos por cierre < USD 3: 1126.
- Excluidos por ADV20_USD < USD 5 millones: 6621.
- Superaron precio y liquidez: 4296.
- Con 61 ruedas y cálculo completo: 4242; sin 61 ruedas: 54.
- BAJISTA_NO_OPERABLE: 1785.
- ESPERANDO_PULLBACK: 2265 (278 debajo de F61.8; 1987 sobre F50).
- EN_ZONA_RR_INSUFICIENTE: 73.
- Lotes fallidos tras reintento: 0.
- Advertencias de enriquecimiento: 9; las filas se conservaron completas con ENGINE_REJECTED o SIN_DATO según el motor.

## Lista compacta

GFR,COO,CB,HMN,PBA,VBK,OC,FDX,TTC,TMUS,ETN,PFBC,ENR,FBK,FLRT,BRK.A,SHOO,FWRG,IJR,FIS,ETD,COUR,CGMM,BOKF,JHMM,FSMD,XBIL,VAC,VTR,AOS,PKG,HIW,AUB,CBRS,NMRK,EUAD,UHAL,NULG,CHD,DEA,CHDN,UHAL.B,DCI,SW,NLY,GGG,AER,KWEB,WBI,SVV,MCHB,UMAC,HYMC,ABVX,FIW,SHW,SILC,BXP,ACVA,AMRX,NEXA,OTTR,WST,APPS,TSAT,IRDM,ARQQ,IGLD,CZR,PRLB,SDVY,CNS,OLMA,VOT,FESM,IWO,IWP,PONY,FTNT,BRK.B,VIOO,GL,CLOA,ALNT,IMCG,CAIE,CWK,PNFP,SPSM,ACHC,CSL,VB,UPWK,PAAA,NBTX,HFWA,RF,VTWO,TREX,IBRX,IWM,SMMD,PFS,JAAA,RDN,BMO,FGI,FSS,SBIL,CM,XRT,XOS,PB,XPON,FIBK,MNKD

Discovery ZIUP_PROXY para monitoreo; no es señal de compra, assessment oficial, ST1–ST4 ni autorización de ejecución.

