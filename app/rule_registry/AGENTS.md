# Rule registry ownership

- This folder owns trusted provider discovery, metadata indexing, eligibility, and
  immutable run snapshots.
- Keep it an in-process library. Do not add HTTP, NATS, PostgreSQL, or rule execution.
- Discover plugins only through `marketbot.rulepacks.v1`; never scan directories or
  import Python paths supplied by YAML.
- Resolve exact semantic versions only. Do not add ranges or implicit latest-version
  selection.
- Changes to lifecycle behavior require matrix tests and compatibility review.
- Do not change `app/contracts` from this folder; raise an RFC to the root integrator.

