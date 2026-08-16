"""Stooq market-data provider — free, no-API-key historical OHLCV.

Fetches daily/weekly/monthly OHLCV from Stooq's CSV endpoint
(``https://stooq.com/q/d/l/?s=<sym>&i=<d|w|m>``) and parses it into the ATLAS
:class:`OHLCV` type. This is **real data**, so ``simulated`` is False.

Design notes:
* Network I/O is isolated behind an injectable ``fetch`` callable, so the
  parsing logic is pure and unit-testable offline. Tests pass a fake fetcher;
  production uses the stdlib ``urllib`` HTTP getter (which honours the standard
  HTTPS_PROXY / CA-bundle environment).
* Stooq serves end-of-day data only via this endpoint — intraday timeframes are
  rejected with a clear error rather than silently returning the wrong thing.
* Malformed or sentinel rows ("N/D") are skipped, not guessed at.
"""
from __future__ import annotations

import io
import os
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import Callable, List, Optional

from ..types import OHLCV, Bar, Provenance, Quote

_STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i={interval}"

# ATLAS timeframe -> Stooq interval code. Stooq's CSV endpoint is EOD only.
_INTERVAL = {"1d": "d", "1w": "w", "1mo": "m", "1M": "m"}

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
    """Fetch a URL as text via stdlib urllib (honours env proxy + CA bundle)."""
    req = urllib.request.Request(url, headers={"User-Agent": "atlas-market-intelligence/0.1"})
    with urllib.request.urlopen(req, timeout=timeout, context=_default_ssl_context()) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


class StooqProvider:
    """Real historical OHLCV from Stooq (free, no key). EOD data only."""

    simulated = False
    source = "stooq"

    def __init__(self, market: str = "us", fetch: Optional[FetchFn] = None, timeout: float = 30.0):
        self.market = market
        self.timeout = timeout
        self._fetch = fetch or (lambda url: _http_get(url, self.timeout))
        self.last_skipped_rows = 0

    # --- symbol / url helpers -------------------------------------------
    def to_stooq_symbol(self, symbol: str) -> str:
        """Map a plain ticker to Stooq's convention (e.g. ``AAPL`` -> ``aapl.us``).

        Symbols that already carry a market suffix (contain ``.``) or an index
        prefix (``^``) are passed through untouched (lower-cased).
        """
        s = symbol.strip().lower()
        if s.startswith("^") or "." in s:
            return s
        return f"{s}.{self.market}"

    def _url(self, symbol: str, timeframe: str) -> str:
        if timeframe not in _INTERVAL:
            raise ValueError(
                f"StooqProvider serves EOD data only; timeframe '{timeframe}' is "
                f"unsupported. Use one of {sorted(_INTERVAL)}."
            )
        return _STOOQ_URL.format(symbol=self.to_stooq_symbol(symbol), interval=_INTERVAL[timeframe])

    # --- parsing (pure, testable) ---------------------------------------
    def parse_csv(self, text: str, symbol: str, timeframe: str) -> OHLCV:
        """Parse a Stooq CSV payload into an OHLCV series.

        Raises ``RuntimeError`` when Stooq returns an error page, a rate-limit
        message, or an empty/no-data response instead of a valid table.
        """
        import csv as _csv

        lower = text.lower()
        head = text.lstrip()[:64].lower()
        if "exceeded" in lower:
            raise RuntimeError("Stooq rate limit hit ('exceeded the daily hits limit'). Try later.")
        if "requires javascript" in lower or ("noindex,nofollow" in lower and "<html" in head):
            raise RuntimeError(
                f"Stooq served a JavaScript bot-verification page for '{symbol}' instead of "
                "CSV — its free download endpoint is now behind an anti-bot wall that a plain "
                "HTTP client cannot pass. Use a keyed provider (e.g. AlphaVantageProvider) or "
                "--csv with a browser-downloaded file."
            )
        if not head.startswith("date,"):
            raise RuntimeError(f"Unexpected Stooq response for '{symbol}': {text[:80]!r}")

        reader = _csv.DictReader(io.StringIO(text))
        bars: List[Bar] = []
        skipped = 0
        for row in reader:
            try:
                ts = datetime.strptime(row["Date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
                v = float(row.get("Volume") or 0.0)
                bars.append(Bar(ts, o, h, l, c, v))
            except (KeyError, ValueError, TypeError):
                skipped += 1  # sentinel ("N/D") or malformed row — skip, don't guess
                continue
        self.last_skipped_rows = skipped
        if not bars:
            raise RuntimeError(f"No usable data rows returned for '{symbol}'.")
        return OHLCV.from_bars(symbol.upper(), timeframe, bars)

    # --- DataProvider interface -----------------------------------------
    def get_ohlcv(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> OHLCV:
        text = self._fetch(self._url(symbol, timeframe))
        series = self.parse_csv(text, symbol, timeframe)
        return series.tail(lookback) if lookback else series

    def get_quote(self, symbol: str) -> Quote:
        """Derive a quote from the latest daily bar (EOD).

        Stooq's free endpoint is end-of-day, so ``staleness_seconds`` on the
        returned quote will honestly reflect that the price is not real-time.
        """
        series = self.get_ohlcv(symbol, "1d", 1)
        last = series.close[-1]
        spread = last * 0.0002
        return Quote(
            symbol=symbol.upper(),
            ts=series.ts[-1],
            last=last,
            bid=last - spread / 2,
            ask=last + spread / 2,
            volume=series.volume[-1],
        )

    def provenance(self, tool: str, symbol: str, timeframe: Optional[str], lookback: Optional[str]) -> Provenance:
        return Provenance(
            tool=tool, symbol=symbol, timeframe=timeframe, lookback=lookback,
            source=self.source, simulated=self.simulated,
        )
