# Synthetic Core rule pack

`synthetic_core` is a trusted, deterministic rule pack for validating the
MarketBot rule platform. It performs no network, filesystem, database, clock,
or entropy access. `EvaluationContext.as_of` is used as the result timestamp.

## Discovery

The repository integrator registers `get_provider()` in the
`marketbot.rulepacks.v1` entry-point group. It returns one manifest and one
executable catalog containing every exact `(rule_id, version)` coordinate.

- `get_providers()` returns a one-element tuple for discovery adapters.
- `get_rules()` returns the executable catalog, keyed by exact `(rule_id, version)`.

The factories deliberately do not import the registry or runtime. Contracts v1
does not define a plugin callable port, so the root integrator/runtime owns the
small adapter from `RulePackProvider` and `RuleRegistration` to its internal
ports. The root `pyproject.toml` must eventually declare the provider entry
point; this engine does not own that file.

## Rules

| Rule | Version | Behavior |
| --- | --- | --- |
| `synthetic.read_number` | `1.0.0` | Reads a `Decimal` or integer context value. |
| `synthetic.multiply` | `1.0.0` | Multiplies exact decimals. |
| `synthetic.threshold` | `1.0.0` | Checks an inclusive minimum. |
| `synthetic.threshold` | `2.0.0` | Checks an inclusive range. |
| `synthetic.exception` | `1.0.0` | Raises an intentional `RuntimeError`. |
| `synthetic.timeout` | `1.0.0` | Spins forever for hard-timeout tests. |

The timeout rule must only run in an isolated subprocess that the runtime can
terminate. Isolation is for operational failure containment, not a security
sandbox; only trusted plugins may be installed.

## Manifests and reproducibility

Both threshold versions live in the same manifest. `RulePackManifest` enforces
uniqueness by exact `(rule_id, version)` coordinate, so versions coexist without
ambiguity.
Every rule is published with lifecycle `PAPER`: the pack may drive PRIMARY and
SHADOW strategies in a PAPER runtime, but it is intentionally ineligible for
LIVE PRIMARY execution until an explicit approval produces new versioned
metadata.
Implementation hashes describe stable rule identity/algorithm data. Manifest
hashes cover every manifest field except the hash itself. Tests compare canonical
results and inventory against checked-in golden files.

## Strategy fixtures

`configs/strategies/synthetic/primary_a.yaml` uses threshold `1.0.0` in `PRIMARY`
mode. `shadow_b.yaml` uses threshold `2.0.0` in `SHADOW` mode for the same pack,
run, engine, family, and input shape. Both files contain data only and are safe-loaded;
they contain exact versions, explicit dependencies, bindings, policies, and
decimal scoring weights that sum exactly to one.

Run the focused suite with:

```powershell
uv run pytest app/rulepacks/synthetic_core/tests
```
