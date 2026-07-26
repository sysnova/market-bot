# Operations

The `marketbot` command is the stable operator entry point. Its `rules`, `strategy`, `audit`,
`supervisor`, and `infra` groups are registered even when an optional operator module is absent. An
absent module reports that fact and never pretends an operation succeeded.

```shell
uv run marketbot --help
uv run marketbot --version
```

Production configuration should be provided as `MARKETBOT_*` environment variables by the runtime.
Do not mount or log `.env` files in production. Treat the URLs as secrets because they may contain
credentials.

JSON logs are the default. Bind a `correlation_id`, message ID, or command ID at entry points and
clear context at the end of every request/message to avoid context leakage between tasks.

`compose.yaml` is solely a workstation convenience. Production NATS and PostgreSQL lifecycle,
backup, TLS, authentication, and monitoring belong to the deployment platform.
