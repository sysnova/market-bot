# MarketBot 7.71.0

Integra los motores candidatos de 7.68.0 con las correcciones de producción de
7.69.0 y 7.70.0. Es la selección predeterminada del CLI y del launcher Linux.

| Motor | Implementación | Reglas | Cambio incluido |
| --- | --- | --- | --- |
| Swing | 16.0.0 | 3.5.0 | Expone el disparador y mínimo del rebote que confirmó la recuperación. |
| Entry Watcher | 5.8.0 | 1.6.0 | Exige aprobación de Swing y conserva stop, objetivo y R/R de la regla disparadora. |
| Entry Opportunity | 22.0.0 | 22.0.0 | Niveles originales, salidas con gaps, fallos de recuperación y protección de ganancias de Core, GERI y SwingTrade. |
| SwingTrade | 1.11.0 | 1.8.0 | Retesteo de soporte local de 1.9 más zona operativa válida de 1.10; migra entradas abiertas o cerradas conservando precio y stop. |
| Intraday | 8.0.0 | 1.4.0 | Correcciones de historial y exclusión de velas REST todavía abiertas. |

Los otros 18 motores conservan la última implementación ya seleccionada en
7.68.0. Los modos activos, programados y bajo demanda se mantienen según el
manifiesto. La actualización no habilita ejecución de órdenes.

SwingTrade 1.10 y 1.11 cargan el historial extendido necesario para momentum al
arrancar. Las referencias exportan una zona por encima de la invalidación;
los niveles Fibonacci siguen disponibles como contexto. Para SE, el caso de
regresión conserva Fibonacci 103.3451–108.8050 y stop 104.1820, y publica la
zona operativa 105.1810–108.8050.

Las definiciones y motores previos permanecen disponibles para rollback
explícito mediante `--definition-path` o `MARKETBOT_DEFINITION_PATH`, según
el punto de entrada utilizado. No se modifican los manifiestos anteriores.
