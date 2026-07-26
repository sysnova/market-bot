# Synthetic strategies

These data-only fixtures exercise PRIMARY versus SHADOW evaluation with two
exact rule versions from the same `synthetic_core` manifest. Load them with a safe YAML loader, then
validate the decoded document as `StrategySpec`. Interpolation tokens are data;
only the strategy runtime may resolve the documented `${steps.*}` references.

The referenced rules have lifecycle `PAPER`. Both fixtures are eligible in a
PAPER runtime; the PRIMARY fixture must not be promoted to LIVE without an
explicit, versioned lifecycle approval.
