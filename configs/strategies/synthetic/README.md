# Synthetic strategies

These data-only fixtures exercise PRIMARY versus CANDIDATE evaluation with two
exact rule versions from the same `synthetic_core` manifest. Load them with a safe YAML loader, then
validate the decoded document as `StrategySpec`. Interpolation tokens are data;
only the strategy runtime may resolve the documented `${steps.*}` references.

The referenced rules have lifecycle `PAPER`. Both fixtures can run in a PAPER
runtime; only PRIMARY can become action-eligible after audit confirmation. Neither
mode implies broker execution.
