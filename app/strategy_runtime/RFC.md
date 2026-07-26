# RFC: exact references in StrategySpec

Status: accepted and implemented in contracts v1 before initial release.

`PipelineStep` now carries required `rule_id` and `rule_version` fields, so YAML strategies spell
the registry coordinate without embedding `@` in an Identifier.

The runtime indexes immutable snapshot entries by `(rule_id, rule_version)`. Multiple versions of
one rule may coexist, and compilation selects only the declared coordinate. The exact reference
and implementation hash are included in the compiled-plan digest.

No latest-version inference or semantic-version ranges are supported.
