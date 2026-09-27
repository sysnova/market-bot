# Validación inicial de SHORT_TACTICAL, 2026-09-26

Se implementó una ruta de observación separada de las decisiones diarias, con
diagnóstico de gates, cotizaciones independientes de trades y estado persistido
para agrupar señales. No se habilitaron compras tácticas ni se cambió la definición
operativa por defecto (7.77.0). La definición 7.78.0 está disponible para selección
explícita y conserva rollback.

## Replay ASTS: 31 de agosto–25 de septiembre

Fuente: barras históricas SIP de Alpaca. Se descargó calentamiento anterior a cada
apertura y se hizo avanzar el reloj al cierre de cada minuto. Se usaron las
implementaciones seleccionadas por `MarketBotAssembly`, con contextos aislados
por rueda. El 7 de septiembre no tuvo sesión y se excluyó.

| Medida | Resultado |
|---|---:|
| Ruedas con datos | 19 |
| Velas regulares de ASTS | 7.410 |
| Lecturas Intraday con madurez SHORT | 39 |
| Movimientos tácticos distintos, agrupados por intención | 27 |
| Observaciones diarias READY (no operaciones distintas) | 14 |
| Observaciones tácticas plenamente READY | 0 |

El cero táctico no demuestra que todos los setups fueran malos: no se incorporó
historia de quotes ejecutables a este replay OHLCV. Por diseño, cotizaciones o
niveles propios del ETF ausentes dejan el candidato bloqueado. Los precios de
las velas no se sustituyeron por bid/ask ficticios.

Como sensibilidad independiente, se siguieron los niveles de los 27 movimientos
en ASTS desde la apertura del minuto disponible siguiente, incluyendo candidatos
que todavía tendrían otros gates pendientes. Hubo **16 stops primero, 9 objetivos
primero y 2 cierres de sesión**. No es un backtest de rentabilidad de la ruta
completa: no incluye fills, spread histórico, comisiones, préstamos SHORT ni los
resultados del ETF. Tampoco es validación fuera de muestra estricta, pues el viernes
25 motivó el diseño. El umbral R/R no se optimizó sobre estos resultados.

## Viernes 25

Las dos lecturas de las velas 13:06 y 13:07 NY se agruparon bajo una intención:
`tactical:ASTS:2026-09-25T17:06:00+00:00`. Conservó la invalidación 61,9345 y el
objetivo 61,5483 de la primera confirmación. La segunda lectura no creó una
entrada adicional ni movió el stop.

En el escenario ideal de disponibilidad al cierre, la apertura de las 13:07 fue
61,77: el R/R restante era 1,3477 y el objetivo se tocó en la vela de las 13:08.
Eso difiere deliberadamente de la auditoría del bus: la primera señal se almacenó
a las 13:07:07,861, por lo que aquella auditoría usó la siguiente vela completa,
13:08, a 61,635 y apenas 0,2895R. Esta diferencia muestra por qué el precio actual
y la demora importan. Ninguno de los escenarios certifica una compra de ASTN.

## Qué cubren las pruebas

- Contratos nuevos aditivos, timestamps y prohibición de habilitar órdenes.
- Quote independiente sin inventar trades; scope, límite de frecuencia y tiempo futuro.
- SHORT táctico con Swing alcista, geometría y R/R insuficiente o no disponible.
- Flujo antiguo no rejuvenecido por quote nueva; confirmación por subida de midpoint.
- Una sola identidad por movimiento, stop y objetivo retenidos, terminalidad y restart.
- Caches que rechazan datos futuros, expiración de quotes y publicación sólo diagnóstica.
- Registro central y selección de nueva definición; versión anterior disponible.
- Seguimiento hipotético que excluye la vela anterior y marca stop/target simultáneos
  como ambiguos, sin inventar el orden intrabar.

## Criterio para la siguiente etapa

Observar sesiones nuevas con el canal de quotes y medir sus cuatro timestamps;
cuantificar gaps y vigencia efectiva de ASTS/ASTN. Evaluar en datos separados
cuántos candidatos pasan todos los gates y su resultado con costos realistas.
Antes de habilitar compras se necesita evidencia del ETF, no sólo del subyacente.
La clasificación READY actual sigue siendo exclusivamente diagnóstica.

Artefactos reproducibles: `.runtime/short-observation-validation/summary.json`,
archivos diarios `ASTS-YYYY-MM-DD-result.json` y caches OHLCV correspondientes.

## Verificación final del repositorio

- `uv run ruff check .`: correcto.
- `uv run pyright`: Windows Application Control bloquea su launcher; el equivalente
  `uv run python -m pyright` terminó con cero errores y cero advertencias.
- `uv run pytest`: **1.866 passed, 11 skipped**, cobertura **79,74%**.
- `uv run marketbot assembly`: confirma que el default efectivo continúa en **7.77.0**.

Se mantuvieron las modificaciones preexistentes ajenas a este cambio. No se
reiniciaron servicios ni se enviaron eventos de replay al bus operativo.
