# Guía para agentes: contratos

Al modificar esta carpeta:

1. Mantener `app.contracts` libre de runtime, registry, IO y persistencia.
2. Escribir o actualizar primero los tests de contrato en `tests/`.
3. Derivar todos los modelos públicos de `StrictFrozenModel`.
4. Usar `Decimal` para valores financieros o puntuaciones; nunca `float`.
5. Exigir timestamps UTC conscientes de zona y hashes mediante `Sha256`.
6. Exportar la superficie soportada desde `app/contracts/__init__.py` y documentar
   cambios públicos en `README.md`.
7. Preservar compatibilidad v1: un cambio que vuelve inválido un mensaje v1 válido
   necesita una nueva versión mayor y una ruta de migración.
8. No agregar dependencias a capas de aplicación. Los contratos pueden depender
   sólo de biblioteca estándar y Pydantic.
