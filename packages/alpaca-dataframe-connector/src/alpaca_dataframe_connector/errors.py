"""Public exceptions raised by the connector."""


class AlpacaDataError(RuntimeError):
    """Alpaca rejected a request or returned malformed market data."""


class AlpacaConfigurationError(ValueError):
    """The connector configuration is incomplete or invalid."""
