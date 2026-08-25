"""Synchronous Alpaca REST client returning scanner-ready pandas DataFrames."""

from __future__ import annotations

import calendar
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import httpx
import pandas as pd

from .config import AlpacaConfig
from .errors import AlpacaDataError

_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
_PERIOD = re.compile(r"^([1-9][0-9]*)(min|m|hour|h|day|d|week|wk|month|mo|y)$")
_INTERVAL = re.compile(r"^([1-9][0-9]*)(min|m|hour|h|day|d|week|wk|month|mo)$")
_RETRIABLE_STATUSES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class _Timeframe:
    api_value: str
    intraday: bool


class AlpacaDataClient:
    """Read historical daily stock bars over REST; never opens a WebSocket."""

    def __init__(
        self,
        config: AlpacaConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)
        self._sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_config(cls, path: str) -> AlpacaDataClient:
        """Construct a client from an Alpaca TOML configuration file."""

        return cls(AlpacaConfig.from_toml(path))

    def __enter__(self) -> AlpacaDataClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the internally-created HTTP connection pool."""

        if self._owns_http_client:
            self._http.close()

    def download(
        self,
        tickers: Sequence[str],
        *,
        period: str = "3y",
        interval: str = "1d",
        now: datetime | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Download bars using yfinance-like ``period`` and ``interval`` values.

        The return value is intentionally a mapping so each scanner invocation receives
        a normal single-ticker DataFrame rather than pandas MultiIndex columns.
        """

        end = now or datetime.now(timezone.utc)
        _require_aware(end, "now")
        start = _period_start(end, period)
        return self.get_bars(tickers, interval=interval, start=start, end=end)

    def get_daily_bars(
        self,
        tickers: Sequence[str],
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, pd.DataFrame]:
        """Backward-compatible shortcut for ``get_bars(..., interval='1d')``."""

        return self.get_bars(tickers, interval="1d", start=start, end=end)

    def get_bars(
        self,
        tickers: Sequence[str],
        *,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, pd.DataFrame]:
        """Return one ascending OHLCV DataFrame per ticker for an explicit range."""

        symbols = _symbols(tickers)
        timeframe = _parse_interval(interval)
        _require_aware(start, "start")
        _require_aware(end, "end")
        if end <= start:
            raise ValueError("end must be later than start")

        records: dict[str, list[Mapping[str, object]]] = {symbol: [] for symbol in symbols}
        for batch in _batched(symbols, self._config.symbols_per_request):
            self._fetch_batch(
                batch,
                timeframe=timeframe.api_value,
                start=start,
                end=end,
                target=records,
            )
        return {
            symbol: _to_dataframe(records[symbol], intraday=timeframe.intraday)
            for symbol in symbols
        }

    def _fetch_batch(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        target: dict[str, list[Mapping[str, object]]],
    ) -> None:
        params = {
            "adjustment": self._config.adjustment,
            "end": _rfc3339(end),
            "feed": self._config.feed,
            "limit": "10000",
            "sort": "asc",
            "start": _rfc3339(start),
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
        }
        seen_tokens: set[str] = set()
        for _ in range(self._config.max_pages):
            payload = self._get_json("/v2/stocks/bars", params)
            raw_bars = payload.get("bars")
            if not isinstance(raw_bars, Mapping):
                raise AlpacaDataError("Alpaca bars response has no bars object")
            for raw_symbol, raw_records in cast(Mapping[object, object], raw_bars).items():
                if not isinstance(raw_symbol, str) or not isinstance(raw_records, list):
                    raise AlpacaDataError("Alpaca bars response is malformed")
                symbol = raw_symbol.strip().upper()
                if symbol not in target:
                    continue
                for record in cast(list[object], raw_records):
                    if not isinstance(record, Mapping):
                        raise AlpacaDataError("Alpaca bar record is malformed")
                    target[symbol].append(cast(Mapping[str, object], record))

            page_token = payload.get("next_page_token")
            if page_token is None:
                return
            if (
                not isinstance(page_token, str)
                or not page_token
                or page_token in seen_tokens
            ):
                raise AlpacaDataError("Alpaca bars next page token is malformed")
            seen_tokens.add(page_token)
            params["page_token"] = page_token
        raise AlpacaDataError(
            f"Alpaca bars pagination exceeded {self._config.max_pages} pages"
        )

    def _get_json(self, path: str, params: dict[str, str]) -> Mapping[str, object]:
        url = f"{self._config.data_base_url.rstrip('/')}{path}"
        headers = {
            "APCA-API-KEY-ID": self._config.api_key_id,
            "APCA-API-SECRET-KEY": self._config.api_secret_key,
        }
        for attempt in range(self._config.max_retries + 1):
            try:
                response = self._http.get(url, headers=headers, params=dict(params))
            except httpx.RequestError as error:
                if attempt >= self._config.max_retries:
                    raise AlpacaDataError("Alpaca market-data request failed") from error
                self._sleep(2.0**attempt)
                continue

            if 200 <= response.status_code < 300:
                try:
                    payload: Any = response.json()
                except ValueError as error:
                    raise AlpacaDataError("Alpaca market-data response is not JSON") from error
                if not isinstance(payload, Mapping):
                    raise AlpacaDataError("Alpaca market-data response must be an object")
                return cast(Mapping[str, object], payload)

            if response.status_code in _RETRIABLE_STATUSES and attempt < self._config.max_retries:
                self._sleep(_retry_delay(response, attempt))
                continue
            detail = response.text.strip().replace("\n", " ")[:200]
            for credential in (
                self._config.api_key_id,
                self._config.api_secret_key,
            ):
                detail = detail.replace(credential, "[REDACTED]")
            suffix = f": {detail}" if detail else ""
            raise AlpacaDataError(
                f"Alpaca market-data request failed with HTTP {response.status_code}{suffix}"
            )
        raise AlpacaDataError("Alpaca market-data request failed")


def _symbols(tickers: Sequence[str]) -> tuple[str, ...]:
    if isinstance(tickers, str):
        raise TypeError("tickers must be a list or tuple of symbols, not one string")
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in tickers))
    if not normalized or any(not symbol for symbol in normalized):
        raise ValueError("at least one non-blank ticker is required")
    return normalized


def _batched(values: tuple[str, ...], size: int) -> Iterator[tuple[str, ...]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _to_dataframe(
    records: Iterable[Mapping[str, object]], *, intraday: bool
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in records:
        try:
            rows.append(
                {
                    "Date": record["t"],
                    "Open": record["o"],
                    "High": record["h"],
                    "Low": record["l"],
                    "Close": record["c"],
                    "Volume": record["v"],
                }
            )
        except KeyError as error:
            raise AlpacaDataError("Alpaca daily bar is missing a required OHLCV field") from error
    if not rows:
        return _empty_dataframe(intraday=intraday)

    dataframe = pd.DataFrame.from_records(rows)
    timestamps = pd.to_datetime(dataframe.pop("Date"), utc=True, errors="coerce")
    if timestamps.isna().any():
        raise AlpacaDataError("Alpaca daily bar contains an invalid timestamp")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        dataframe[column] = pd.to_numeric(dataframe[column], errors="coerce")
    if dataframe[_COLUMNS].isna().any(axis=None):
        raise AlpacaDataError("Alpaca daily bar contains an invalid OHLCV value")

    market_timestamps = timestamps.dt.tz_convert("America/New_York")
    if intraday:
        dataframe.index = pd.DatetimeIndex(market_timestamps, name="Datetime")
    else:
        dataframe.index = pd.DatetimeIndex(
            market_timestamps.dt.normalize().dt.tz_localize(None), name="Date"
        )
    dataframe = dataframe[_COLUMNS]
    dataframe = dataframe[~dataframe.index.duplicated(keep="last")].sort_index()
    return dataframe


def _empty_dataframe(*, intraday: bool) -> pd.DataFrame:
    dataframe = pd.DataFrame(
        {
            "Open": pd.Series(dtype="float64"),
            "High": pd.Series(dtype="float64"),
            "Low": pd.Series(dtype="float64"),
            "Close": pd.Series(dtype="float64"),
            "Volume": pd.Series(dtype="int64"),
        }
    )
    if intraday:
        dataframe.index = pd.DatetimeIndex(
            [], tz="America/New_York", name="Datetime"
        )
    else:
        dataframe.index = pd.DatetimeIndex([], name="Date")
    return dataframe


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _subtract_years(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _subtract_months(value: datetime, months: int) -> datetime:
    target_month = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(target_month, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _period_start(end: datetime, period: str) -> datetime:
    normalized = period.strip().lower()
    if normalized == "ytd":
        return end.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    if normalized == "max":
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    matched_period = _PERIOD.fullmatch(normalized)
    if matched_period is None:
        raise ValueError(
            "period must be a duration such as 90m, 12h, 5d, 2wk, 3mo or 3y; "
            "ytd and max are also supported"
        )
    amount = int(matched_period.group(1))
    unit = matched_period.group(2)
    if unit in {"min", "m"}:
        return end - timedelta(minutes=amount)
    if unit in {"hour", "h"}:
        return end - timedelta(hours=amount)
    if unit in {"day", "d"}:
        return end - timedelta(days=amount)
    if unit in {"week", "wk"}:
        return end - timedelta(weeks=amount)
    if unit in {"month", "mo"}:
        return _subtract_months(end, amount)
    return _subtract_years(end, amount)


def _parse_interval(interval: str) -> _Timeframe:
    normalized = interval.strip().lower()
    matched_interval = _INTERVAL.fullmatch(normalized)
    if matched_interval is None:
        raise ValueError(
            "interval must use minutes, hours, days, weeks or months, for example "
            "1m, 15m, 1h, 4h, 1d, 1wk or 1mo"
        )

    amount = int(matched_interval.group(1))
    unit = matched_interval.group(2)
    if unit in {"min", "m"}:
        if amount > 59:
            raise ValueError("interval minutes must be between 1 and 59")
        return _Timeframe(f"{amount}Min", intraday=True)
    if unit in {"hour", "h"}:
        if amount > 23:
            raise ValueError("interval hours must be between 1 and 23")
        return _Timeframe(f"{amount}Hour", intraday=True)
    if unit in {"day", "d"}:
        if amount != 1:
            raise ValueError("interval days only support 1d")
        return _Timeframe("1Day", intraday=False)
    if unit in {"week", "wk"}:
        if amount != 1:
            raise ValueError("interval weeks only support 1wk")
        return _Timeframe("1Week", intraday=False)
    if amount not in {1, 2, 3, 6, 12}:
        raise ValueError("interval months support 1mo, 2mo, 3mo, 6mo or 12mo")
    return _Timeframe(f"{amount}Month", intraday=False)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return 2.0**attempt
