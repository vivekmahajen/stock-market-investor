"""Yahoo Finance market-data provider — free, no-API-key, long history.

Fetches OHLCV from Yahoo's public chart endpoint
(``https://query1.finance.yahoo.com/v8/finance/chart/<symbol>``) and parses the
JSON into the ATLAS :class:`OHLCV` type. This is **real data**, so ``simulated``
is False.

Why this endpoint: unlike Stooq's CSV download (now behind an anti-bot wall) and
Alpha Vantage's free tier (capped at ~100 daily bars), Yahoo's chart API serves
decades of daily history for free and tolerates a plain HTTP client with a
browser-like User-Agent. That long history is what the walk-forward skill check
needs to produce a many-fold, non-noise-dominated measurement.

Design notes mirror the other providers:
* Network I/O is isolated behind an injectable ``fetch`` callable, so parsing is
  pure and unit-testable offline.
* Yahoo returns a JSON error envelope (not an HTTP error) for bad symbols and
  rate limits — those are detected and raised with a clear message.
* Rows Yahoo reports as ``null`` (holidays, halts) are skipped, not guessed.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone
from typing import Callable, List, Optional

from ..types import OHLCV, Bar, Provenance, Quote

_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"

# ATLAS timeframe -> Yahoo interval code.
_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m",
    "1d": "1d", "1w": "1wk", "1mo": "1mo", "1M": "1mo",
}
_INTRADAY_CODES = ("1m", "5m", "15m", "30m", "60m")
# Approximate calendar days per bar, to size an explicit daily-resolution window.
_CAL_DAYS_PER_BAR = {"1d": 1.6, "1wk": 8.0, "1mo": 32.0}

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
    # A browser-like User-Agent avoids Yahoo's default-client throttling.
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_default_ssl_context()) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _intraday_range(interval: str) -> str:
    """Bounded range Yahoo serves at true intraday resolution."""
    return "730d" if interval == "60m" else "60d"


class YahooProvider:
    """Real historical OHLCV from Yahoo Finance (free, no key). Long history.

    For end-of-day intervals the URL uses an explicit ``period1``/``period2``
    window rather than ``range=max``: Yahoo silently *downsamples* ``range=max``
    for long histories (returning monthly/quarterly points labelled ``1d``),
    which would wreck any volatility estimate. An explicit period window is served
    at true daily resolution — the same approach yfinance uses.
    """

    simulated = False
    source = "yahoo"
    # Default daily-resolution window (calendar days) when the full history is
    # requested (lookback=0). ~30 years of daily bars — long enough for a
    # many-fold skill check, short enough that Yahoo keeps it at daily resolution.
    _FULL_WINDOW_DAYS = 30 * 366

    def __init__(self, fetch: Optional[FetchFn] = None, timeout: float = 30.0):
        self.timeout = timeout
        self._fetch = fetch or (lambda url: _http_get(url, self.timeout))
        self.last_skipped_rows = 0

    def _now_unix(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    def _url(self, symbol: str, timeframe: str, lookback: int = 0) -> str:
        if timeframe not in _INTERVAL:
            raise ValueError(
                f"Unsupported timeframe '{timeframe}'. Use one of {sorted(_INTERVAL)}."
            )
        interval = _INTERVAL[timeframe]
        sym = symbol.strip().upper()
        if interval in _INTRADAY_CODES:
            # Intraday history is short; Yahoo serves it correctly via range=.
            query = f"range={_intraday_range(interval)}&interval={interval}"
            return _CHART_BASE.format(symbol=sym, query=query)
        # EOD: explicit period window at TRUE resolution (avoids range=max downsampling).
        period2 = self._now_unix() + 86400
        if lookback and lookback > 0:
            span_days = int(lookback * _CAL_DAYS_PER_BAR.get(interval, 1.6)) + 400
        else:
            span_days = self._FULL_WINDOW_DAYS
        period1 = max(0, period2 - span_days * 86400)
        query = (f"period1={period1}&period2={period2}&interval={interval}"
                 f"&includeAdjustedClose=true")
        return _CHART_BASE.format(symbol=sym, query=query)

    # --- parsing (pure, testable) ---------------------------------------
    def parse_json(self, text: str, symbol: str, timeframe: str) -> OHLCV:
        """Parse a Yahoo chart JSON payload into an OHLCV series.

        Raises ``RuntimeError`` on Yahoo's error envelope, a rate-limit page, or a
        payload missing the expected quote arrays.
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            head = text.lstrip()[:80]
            raise RuntimeError(f"Unexpected non-JSON Yahoo response for '{symbol}': {head!r}")

        chart = data.get("chart") or {}
        err = chart.get("error")
        if err:
            desc = err.get("description") if isinstance(err, dict) else err
            raise RuntimeError(f"Yahoo error for '{symbol}': {desc}")
        results = chart.get("result") or []
        if not results:
            raise RuntimeError(f"No data returned by Yahoo for '{symbol}'.")

        res = results[0]
        stamps = res.get("timestamp") or []
        indicators = res.get("indicators") or {}
        quote = (indicators.get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        # Yahoo's `close` is UNADJUSTED — every stock split shows up as a huge
        # one-day jump that would explode a volatility/returns model. The
        # `adjclose` series is split- and dividend-adjusted (continuous), so we
        # back-adjust OHLC by the per-bar factor adjclose/close and use it as the
        # canonical price. This is the single most important correctness choice
        # for the forecaster: without it, split days masquerade as ±90% returns.
        adjclose = ((indicators.get("adjclose") or [{}])[0].get("adjclose")) or []
        have_adj = any(x is not None for x in adjclose)
        if not stamps or not closes:
            raise RuntimeError(f"Yahoo payload for '{symbol}' had no price series.")

        intraday = timeframe in ("1m", "5m", "15m", "30m", "1h")
        bars: List[Bar] = []
        skipped = 0
        for i, ts in enumerate(stamps):
            try:
                o, h, l, c = opens[i], highs[i], lows[i], closes[i]
                if None in (o, h, l, c):
                    skipped += 1  # holiday/halt row Yahoo pads with nulls
                    continue
                # Use the adjusted series when available. If adjclose exists for
                # the symbol but is null on THIS bar, skip the bar rather than
                # fall back to the raw close — mixing adjusted and unadjusted
                # prices in one series would manufacture split-sized fake returns.
                ai = adjclose[i] if i < len(adjclose) else None
                if have_adj and ai is None:
                    skipped += 1
                    continue
                a = ai if ai is not None else c
                factor = (a / c) if c else 1.0  # split/dividend back-adjust factor
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                if not intraday:
                    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
                v = vols[i] if i < len(vols) and vols[i] is not None else 0.0
                bars.append(Bar(dt, float(o) * factor, float(h) * factor,
                                float(l) * factor, float(a), float(v)))
            except (IndexError, TypeError, ValueError):
                skipped += 1
                continue
        self.last_skipped_rows = skipped
        if not bars:
            raise RuntimeError(f"No usable data rows returned by Yahoo for '{symbol}'.")
        return OHLCV.from_bars(symbol.upper(), timeframe, bars)

    # --- DataProvider interface -----------------------------------------
    def get_ohlcv(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> OHLCV:
        text = self._fetch(self._url(symbol, timeframe, lookback))
        series = self.parse_json(text, symbol, timeframe)
        self._check_resolution(series, symbol, timeframe)
        return series.tail(lookback) if lookback else series

    @staticmethod
    def _check_resolution(series: OHLCV, symbol: str, timeframe: str) -> None:
        """Guard against Yahoo silently downsampling (monthly points as '1d').

        A daily series has a median bar-gap of 1 day (weekends/holidays aside);
        if the median gap is many days, the payload is coarser than requested and
        every volatility number derived from it would be nonsense.
        """
        expected = {"1d": 1, "1w": 7, "1mo": 31}.get(timeframe)
        if expected is None or len(series) < 3:
            return
        ts = series.ts
        gaps = sorted((ts[i] - ts[i - 1]).days for i in range(1, len(ts)))
        median_gap = gaps[len(gaps) // 2]
        if median_gap > expected * 3 + 2:
            raise RuntimeError(
                f"Yahoo returned coarser-than-{timeframe} data for '{symbol}' "
                f"(median bar gap {median_gap}d, expected ~{expected}d) — the feed "
                f"downsampled the request. Refusing rather than compute volatility "
                f"from mislabelled bars."
            )

    def get_quote(self, symbol: str) -> Quote:
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
