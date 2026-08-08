# Rule registry

`app.rule_registry` is a shared, in-process library. It indexes trusted rule-pack
metadata and creates an immutable, content-addressed snapshot for each run. It is
not a service and it never executes rules.

Providers are registered explicitly with `Registry.register`. Installed providers
may be discovered only through the versioned `marketbot.rulepacks.v1` entry-point
group. Strategy files cannot name Python modules or trigger filesystem scans.

References use exact `rule_id@major.minor.patch` syntax. The registry rejects
duplicate references, altered manifests, and providers that target a contracts
major version other than `1` (`1.0.0` remains accepted as an explicit alias).

## Eligibility

| Mode/environment | Eligible lifecycle states |
| --- | --- |
| `PRIMARY` / `LIVE` | `APPROVED` |
| `PRIMARY` / `PAPER` | `PAPER`, `APPROVED` |
| `CANDIDATE` | `VALIDATED`, `PAPER`, `APPROVED` |
| `RESEARCH` | `DRAFT`, `VALIDATED`, `PAPER`, `APPROVED` |

Using `DRAFT` in research emits a warning. `DEPRECATED` is rejected everywhere
unless the caller sets `allow_deprecated_replay=True`, which is intended only for
an explicit historical replay. `DISABLED` never resolves rules.

Manifest hashes cover the complete canonical metadata, including timestamps and
each implementation hash, but exclude the manifest digest itself. Providers should call
`calculate_manifest_hash` when constructing their frozen manifest.

## Verification

```powershell
uv run pytest app/rule_registry/tests
uv run ruff check app/rule_registry
uv run pyright app/rule_registry
```
