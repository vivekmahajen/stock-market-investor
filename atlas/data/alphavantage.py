"""Alpha Vantage market-data provider — real OHLCV via a free API key.

Fetches daily/weekly/monthly and intraday series from Alpha Vantage's ``query``
endpoint (``datatype=csv``) and parses them into the ATLAS :class:`OHLCV` type.
Real data, so ``simulated`` is False.

Design mirrors :class:`~atlas.data.stooq.StooqProvider`:
* Network I/O is isolated behind an injectable ``fetch`` callable, so parsing
  and error handling are unit-tested fully offline.
* Alpha Vantage returns *JSON* (not CSV) on errors and rate limits even when
  ``datatype=csv`` is requested — those are detected and raised as clear errors
  instead of being mis-parsed.

Get a free key (instant, no card) at https://www.alphavantage.co/support/#api-key
and set ``ALPHAVANTAGE_API_KEY`` or pass ``api_key=``. Free tier is limited
(about 25 requests/day), so this suits single-symbol lookups more than wide
screens.
"""
from __future__ import annotations

import io
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, List, Optional

from ..types import OHLCV, Bar, Provenance, Quote

_BASE = "https://www.alphavantage.co/query"

# ATLAS timeframe -> Alpha Vantage TIME_SERIES_* function (EOD/weekly/monthly).
_SERIES_FUNC = {
    "1d": "TIME_SERIES_DAILY",
    "1w": "TIME_SERIES_WEEKLY",
    "1mo": "TIME_SERIES_MONTHLY",
    "1M": "TIME_SERIES_MONTHLY",
}
# ATLAS timeframe -> Alpha Vantage intraday interval.
_INTRADAY = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min"}

FetchFn = Callable[[str], str]


def _default_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        path = os.environ.get(var)
        if path and os.path.exists(path):
            try:
                ctx.load_verify_locations(path)
            except ssl.SSLError:
                pass
    return ctx


def _http_get(url: str, timeout: float = 30.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "atlas-market-intelligence/0.1"})
    with urllib.request.urlopen(req, timeout=timeout, context=_default_ssl_context()) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


class AlphaVantageProvider:
    """Real OHLCV from Alpha Vantage. Requires a free API key."""

    simulated = False
    source = "alphavantage"

    def __init__(
        self,
        api_key: Optional[str] = None,
        fetch: Optional[FetchFn] = None,
        timeout: float = 30.0,
        premium: bool = False,
        min_interval: float = 1.2,
    ):
        self.api_key = api_key or os.environ.get("ALPHAVANTAGE_API_KEY")
        self.timeout = timeout
        # outputsize=full is a premium-only feature on Alpha Vantage; the free
        # tier is limited to 'compact' (latest ~100 bars). Default to free.
        self.premium = premium
        # Free tier caps bursts at ~1 request/second; space real calls out so a
        # multi-call analyze (prices + fundamentals + news) doesn't self-throttle.
        self.min_interval = 0.0 if premium else min_interval
        self._last_call = 0.0
        self._injected = fetch is not None
        self._fetch = fetch or (lambda url: _http_get(url, self.timeout))

    def _do_fetch(self, url: str) -> str:
        """Fetch with per-second rate limiting (skipped for injected test fetchers)."""
        if not self._injected and self.min_interval > 0:
            elapsed = time.time() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.time()
        return self._fetch(url)

    def _require_key(self) -> None:
        if not self._injected and not self.api_key:
            raise ValueError(
                "Alpha Vantage API key required. Get a free key at "
                "https://www.alphavantage.co/support/#api-key and set the "
                "ALPHAVANTAGE_API_KEY environment variable or pass api_key=."
            )

    def _url(self, symbol: str, timeframe: str, lookback: int) -> str:
        params = {"symbol": symbol.upper(), "apikey": self.api_key or "demo", "datatype": "csv"}
        want_full = self.premium and lookback > 100  # 'full' is premium-only
        if timeframe in _INTRADAY:
            params["function"] = "TIME_SERIES_INTRADAY"
            params["interval"] = _INTRADAY[timeframe]
            params["outputsize"] = "full" if want_full else "compact"
        elif timeframe in _SERIES_FUNC:
            params["function"] = _SERIES_FUNC[timeframe]
            if timeframe == "1d":
                params["outputsize"] = "full" if want_full else "compact"
        else:
            raise ValueError(
                f"Unsupported timeframe '{timeframe}'. Use one of "
                f"{sorted(set(_SERIES_FUNC) | set(_INTRADAY))}."
            )
        return f"{_BASE}?{urllib.parse.urlencode(params)}"

    # --- parsing (pure, testable) ---------------------------------------
    def parse_csv(self, text: str, symbol: str, timeframe: str) -> OHLCV:
        """Parse an Alpha Vantage CSV payload into an OHLCV series.

        Detects the JSON error/rate-limit responses Alpha Vantage returns even
        for ``datatype=csv`` and raises a clear error rather than mis-parsing.
        """
        import csv as _csv

        stripped = text.lstrip()
        if stripped.startswith("{"):
            raise RuntimeError(_explain_json(stripped, symbol))
        if not stripped.lower().startswith("timestamp,"):
            raise RuntimeError(f"Unexpected Alpha Vantage response for '{symbol}': {text[:120]!r}")

        reader = _csv.DictReader(io.StringIO(stripped))
        bars: List[Bar] = []
        for row in reader:
            try:
                bars.append(
                    Bar(
                        ts=_parse_ts(row["timestamp"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume") or 0.0),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        if not bars:
            raise RuntimeError(f"No usable data rows returned for '{symbol}'.")
        # Alpha Vantage returns newest-first; OHLCV.from_bars sorts ascending.
        return OHLCV.from_bars(symbol.upper(), timeframe, bars)

    # --- DataProvider interface -----------------------------------------
    def get_ohlcv(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> OHLCV:
        self._require_key()
        text = self._do_fetch(self._url(symbol, timeframe, lookback))
        series = self.parse_csv(text, symbol, timeframe)
        return series.tail(lookback) if lookback else series

    def get_quote(self, symbol: str) -> Quote:
        self._require_key()
        params = {"function": "GLOBAL_QUOTE", "symbol": symbol.upper(),
                  "apikey": self.api_key or "demo", "datatype": "csv"}
        text = self._do_fetch(f"{_BASE}?{urllib.parse.urlencode(params)}")
        return self.parse_quote(text, symbol)

    def parse_quote(self, text: str, symbol: str) -> Quote:
        import csv as _csv

        stripped = text.lstrip()
        if stripped.startswith("{"):
            raise RuntimeError(_explain_json(stripped, symbol))
        rows = list(_csv.DictReader(io.StringIO(stripped)))
        if not rows:
            raise RuntimeError(f"No quote returned for '{symbol}'.")
        r = rows[0]
        last = float(r["price"])
        spread = last * 0.0002
        ts = _parse_ts(r.get("latestDay", "")) if r.get("latestDay") else datetime.now(timezone.utc)
        return Quote(
            symbol=symbol.upper(), ts=ts, last=last,
            bid=last - spread / 2, ask=last + spread / 2,
            volume=float(r.get("volume") or 0.0),
        )

    # --- fundamentals & sentiment (JSON endpoints) ----------------------
    def get_fundamentals(self, symbol: str) -> dict:
        """Company OVERVIEW (valuation, margins, growth). Returns the raw object."""
        self._require_key()
        params = {"function": "OVERVIEW", "symbol": symbol.upper(), "apikey": self.api_key or "demo"}
        obj = self._json(f"{_BASE}?{urllib.parse.urlencode(params)}", symbol)
        if not obj or "Symbol" not in obj:
            raise RuntimeError(f"No fundamentals returned for '{symbol}' (unknown symbol or empty overview).")
        return obj

    def get_news_sentiment(self, symbol: str, window: int = 50) -> dict:
        """NEWS_SENTIMENT feed for a ticker (latest first). Returns the raw object."""
        self._require_key()
        params = {
            "function": "NEWS_SENTIMENT", "tickers": symbol.upper(),
            "apikey": self.api_key or "demo", "sort": "LATEST", "limit": max(1, min(window, 1000)),
        }
        return self._json(f"{_BASE}?{urllib.parse.urlencode(params)}", symbol)

    def get_earnings_calendar(self, symbol: str, horizon: str = "3month") -> list:
        """Upcoming earnings dates for a ticker (EARNINGS_CALENDAR, CSV). Returns
        parsed rows (possibly empty)."""
        self._require_key()
        from ..events import parse_earnings_csv

        params = {"function": "EARNINGS_CALENDAR", "symbol": symbol.upper(),
                  "horizon": horizon, "apikey": self.api_key or "demo"}
        text = self._do_fetch(f"{_BASE}?{urllib.parse.urlencode(params)}")
        if text.lstrip().startswith("{"):
            raise RuntimeError(_explain_json(text, symbol))
        return parse_earnings_csv(text)

    def _json(self, url: str, symbol: str) -> dict:
        text = self._do_fetch(url)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError(f"Alpha Vantage returned non-JSON for '{symbol}': {text[:120]!r}")
        if isinstance(obj, dict):
            for key in ("Error Message", "Note", "Information"):
                if key in obj:
                    raise RuntimeError(_explain_json(text, symbol))
        return obj

    def provenance(self, tool: str, symbol: str, timeframe: Optional[str], lookback: Optional[str]) -> Provenance:
        return Provenance(
            tool=tool, symbol=symbol, timeframe=timeframe, lookback=lookback,
            source=self.source, simulated=self.simulated,
        )


def _parse_ts(value: str) -> datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp '{value}'")


def _explain_json(text: str, symbol: str) -> str:
    """Turn an Alpha Vantage JSON error/note into an actionable message."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return f"Alpha Vantage returned a non-CSV response for '{symbol}': {text[:120]!r}"
    for key in ("Error Message", "Note", "Information"):
        if key in obj:
            msg = obj[key]
            if key in ("Note", "Information"):
                return f"Alpha Vantage rate limit / notice for '{symbol}': {msg}"
            return f"Alpha Vantage error for '{symbol}': {msg}"
    return f"Alpha Vantage returned an unexpected object for '{symbol}': {text[:120]!r}"
