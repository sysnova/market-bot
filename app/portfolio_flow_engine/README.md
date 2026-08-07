# Portfolio Flow engine

La implementaciÃ³n V1 conserva solamente la protecciÃ³n por presiÃ³n vendedora. V2 agrega la
clasificaciÃ³n y alerta simÃ©trica de presiÃ³n compradora. Sus polÃ­ticas inmutables viven en
`configs/rules/portfolio_flow/1.0.0.yaml` y `2.0.0.yaml`; la versiÃ³n activa se elige desde la
definiciÃ³n general de MarketBot, no dentro del proceso.

Portfolio Flow v2 procesa quotes y trades efímeros de Alpaca dentro de una ventana móvil de tres
minutos. No envía órdenes ni conserva historial de largo plazo.

Clasifica como venta agresiva una operación ejecutada al bid o por debajo, y como compra agresiva
una operación ejecutada al ask o por encima. Las operaciones dentro del spread son neutrales y no
se atribuyen a ningún lado.

Con la política predeterminada:

- `PROTECT` exige al menos 20 trades, 1.000 acciones, 70% de volumen vendedor y una caída de 0,30%
  dentro de la ventana;
- `AGGRESSIVE ENTRY WATCH` exige el mismo piso de actividad, 70% de volumen comprador y una suba
  de 0,30%;
- cada lado tiene cooldown independiente de cinco minutos;
- los bloques de al menos 1.000 acciones se informan como evidencia adicional.

La presión compradora se publica con `AlertKind.PORTFOLIO_FLOW_BUY`, color cyan y una alarma corta
de dos tonos en el monitor de compras/protección. Es una entrada agresiva en observación, sin nivel
L1-L4: estructura, precio eficiente e invalidación todavía deben confirmarse por los engines
correspondientes.
