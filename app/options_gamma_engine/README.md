# Options Gamma Engine

Engine puro y deterministico que convierte snapshots de opciones en contexto tactico. No conoce
Alpaca, NATS, PostgreSQL, alertas ni ejecucion de ordenes; esas responsabilidades viven en
`app/integration/`.

## Entrada y salida

`OptionsGammaContext` recibe spot, ventana de vencimientos y una tupla provider-neutral de
`OptionContractSnapshot`. `OptionsGammaEngine.evaluate()` devuelve el contrato estable
`GammaAssessment`. `gamma_analysis_from_assessment()` lo proyecta a
`AnalysisResult(OPTIONS_GAMMA)` para el pipeline analitico existente.

El calculo incluye GEX neto y absoluto, call/put/absolute walls, max pain, gamma flip aproximado,
expected move, regimen, riesgo de pin/aceleracion y resultados por vencimiento. La metodologia v1
declara la hipotesis `CALL_POSITIVE_PUT_NEGATIVE`; no afirma conocer el inventario real de dealers.

## Limites

- Los niveles son escenarios y zonas de influencia, no precios objetivo garantizados.
- Open interest puede tener frecuencia diaria aunque gamma, IV, primas y spot cambien intradia.
- Contextos con cobertura insuficiente se marcan degradados o no disponibles.
- Gamma nunca crea una compra, una orden ni una madurez L1-L4.
- Los consumidores deben respetar `expires_at`, calidad y deriva del spot.

## Tests

```powershell
uv run pytest app/options_gamma_engine/tests app/contracts/tests/test_options_gamma.py --no-cov -q
```
