# ATLAS — Unified Market-Intelligence Agent

**ATLAS** (Automated Technical, Learning & Analytical System) is a system-prompt
specification for a tool-grounded, market-intelligence LLM agent covering
equities, ETFs, futures, FX, and crypto. It turns raw market data into
**decision-grade, fully-explained analysis** — automated technical analysis,
chart-pattern detection, ranked and risk-defined signals, portfolio
construction, backtests, and monitoring — in plain language a human can act on
and audit.

> **Educational analysis, not financial advice.** ATLAS is a decision-support
> analyst, not a fiduciary and not a fortune teller. Every actionable output is
> a hypothesis with a probability, an explicit risk, and an explicit way it can
> be wrong. A system prompt cannot beat the market by itself — see
> [Where the real edge comes from](#where-the-real-edge-comes-from).

## What's in this repo

| Path | Purpose |
|---|---|
| [`prompts/atlas-system-prompt.md`](prompts/atlas-system-prompt.md) | The **deployable** system prompt — Sections 1–19 only. Paste into the system/developer role of a function-calling LLM. |
| [`docs/atlas-spec.md`](docs/atlas-spec.md) | The **full specification** — operator notes, the system prompt, and Appendices A (competitive coverage) & B (candor on edge). The canonical reference. |
| [`atlas/`](atlas/) | The **reference compute layer** — a pure-Python implementation of the Section 3 tools (indicators, levels, patterns, backtesting, seasonality, risk sizing, ATLAS Score). See [The reference implementation](#the-reference-implementation). |
| [`tests/`](tests/) | pytest suite covering the compute layer against hand-computed values. |
| `README.md` | This file: overview, the required tool surface, and how to deploy. |

## Design principle: tool-grounded, never imagined

The single most important design choice is that **the agent computes nothing
from imagination**. Every price, indicator value, pattern, fundamental, and
backtest statistic comes from a tool call. If it didn't come from a tool, ATLAS
doesn't state it. This is what makes the output trustworthy instead of a
confident hallucination.

The prompt enforces a strict set of guardrails (Section 2 of the spec):

- **No fabrication** — all quantitative claims trace to a tool result.
- **No guarantees** — probabilistic language only, always with sample size and lookback.
- **Not financial advice** — a non-boilerplate reminder on any action recommendation.
- **Risk before reward** — no entry without a stop, invalidation, size, and worst-case currency loss.
- **No manipulation or illegality** — refuses pump-and-dump, spoofing, MNPI trading, etc.
- **Suitability** — adapts to the user's risk tolerance, horizon, capital, jurisdiction, experience.
- **Disclosed uncertainty** — stale/thin/conflicting data lowers confidence; "not enough to call this" is a valid answer.

## Required tool surface

ATLAS acts only through tools. To deploy it, wire up function-calling for at
least the following (full signatures in Section 3 of the spec). Without these,
the agent degrades gracefully and states what it cannot do.

**Market data**
- `get_ohlcv(symbol, timeframe, lookback)` — OHLCV time series (1m…1M).
- `get_quote(symbol)` — last price, bid/ask, spread, volume, timestamp.
- `get_fundamentals(symbol)` — valuation, growth, margins, balance sheet, estimates.
- `get_options_chain(symbol, expiry?)` — strikes, IV, greeks, open interest.
- `get_news_sentiment(symbol, window)` — headlines, source, timestamp, scored sentiment.
- `get_calendar(symbol|market, window)` — earnings, dividends, splits, macro releases.

**Compute**
- `compute_indicators(series, [indicator_specs])`
- `detect_patterns(series, [pattern_families])`
- `detect_levels(series)`
- `run_backtest(strategy_spec, universe, period, costs)`
- `run_screen(filter_spec, universe)`
- `optimize_portfolio(holdings, constraints, objective)`
- `compute_seasonality(symbol, granularity)`

**Action (guarded)**
- `create_alert(symbol, condition, channel)` — persists a monitoring rule; never auto-executes.
- `paper_trade(order)` — simulated fill only. **ATLAS never places real orders.**

## Capabilities

| Area | Spec section |
|---|---|
| Multi-timeframe technical analysis, indicators, dynamic levels, confluence, regime detection | 4 |
| Chart-pattern / candlestick / harmonic / Fibonacci recognition with targets & base rates | 5 |
| Ranked, risk-defined signals with confidence, invalidation, and a counter-case | 6 |
| Natural-language screening with transparent, echoed-back criteria | 7 |
| Backtesting with realistic costs, out-of-sample separation, and overfitting checks | 8 |
| Position sizing, portfolio heat, volatility scaling, drawdown guardrails | 9 |
| Explainable 0–100 ATLAS Score with full factor attribution | 10 |
| Portfolio construction, optimization, rebalancing, benchmark comparison | 11 |
| Sentiment/news/event fusion as context and risk | 12 |
| Dynamic alerts & monitoring (notifications, never auto-trades) | 13 |

See [Appendix A](docs/atlas-spec.md#appendix-a--competitive-coverage-matrix-operator-reference)
for how each area maps against TrendSpider, Trade Ideas, Tickeron, Autochartist,
TradingView, Danelfin, Incite AI, Prospero.ai, and Magnifi.

## The reference implementation

The [`atlas/`](atlas/) package is a working, **dependency-free** (pure standard
library) implementation of the compute layer the prompt calls — the "real
indicator / pattern / backtest compute layer" that
[Appendix B](docs/atlas-spec.md#appendix-b--where-the-real-edge-has-to-come-from-candor)
names as a genuine requirement. It is deliberately faithful to the spec's
discipline: it computes only from the data it is given and never fabricates.

| Module | Implements | Spec section |
|---|---|---|
| `atlas/indicators.py` | SMA/EMA/WMA, RSI, MACD, ATR, Bollinger, Stochastic, ADX/DMI, OBV, ROC, VWAP, relative volume | 4 |
| `atlas/levels.py` | Swing pivots, clustered support/resistance, nearest levels | 4 |
| `atlas/patterns.py` | Candlestick recognition (doji, hammer, engulfing, marubozu, …) with geometric confidence | 5 |
| `atlas/chart_patterns.py` | Classical patterns: double top/bottom, head-and-shoulders (+ inverse), triangles — with measured target, invalidation, completion | 5 |
| `atlas/harmonics.py` | Harmonic patterns (Gartley, Bat, Butterfly, Crab, Shark) via Fibonacci leg ratios, with PRZ, targets, invalidation | 5 |
| `atlas/fibonacci.py` | Auto-anchored Fibonacci retracements & extensions | 5 |
| `atlas/risk.py` | Position sizing, R-multiple, portfolio heat, capped fractional Kelly | 9 |
| `atlas/backtest.py` | Next-bar-open, cost-aware backtester + full metric set + blunt verdict | 8 |
| `atlas/seasonality.py` | Calendar-bucketed return stats **with sample sizes** | 3 |
| `atlas/scoring.py` | Explainable 0–100 ATLAS Score with attribution | 10 |
| `atlas/fundamentals.py` | Fundamental & news-sentiment sub-scores from a data feed, with factor attribution | 10, 12 |
| `atlas/screen.py` | Transparent, filterable, composite-ranked screener with liquidity flags | 7 |
| `atlas/portfolio.py` | Pure-Python optimizer (equal-weight / inverse-vol / min-variance / max-Sharpe), correlation, beta, stress test | 11 |
| `atlas/alerts.py` | Persistent alert store with static & dynamic (ATR/indicator) conditions — **never auto-trades** | 13 |
| `atlas/calibration.py` | Signal-confidence vs. realized-outcome tracker: reliability buckets, Brier score, ECE | Appendix B |
| `atlas/analysis.py` | Regime classification, confluence score, the Section 15 output envelope | 4, 6, 14, 15 |
| `atlas/data/` | `DataProvider` seam: `AlphaVantageProvider` (real, free key), `StooqProvider`, `CSVProvider` (your files), and a seeded `SyntheticProvider` flagged `simulated=True` | 3 |
| `atlas/tools.py` | The Section 3 function-calling registry over the above | 3 |

Design guarantees that mirror the guardrails:

- **No fabricated data.** Synthetic/demo data is always flagged
  `data_is_simulated: true` in provenance — it can never be mistaken for a real feed.
- **No fabricated sub-scores.** With no news/fundamentals feed wired in, the
  `sentiment` and `fundamental` sub-scores return `null`, not a made-up number.
- **Sample honesty.** The backtester flags any run under 30 trades as noise and
  the `verdict()` refuses to call a tiny sample an edge.
- **Risk before reward.** `size_position` caps risk per trade and returns the
  worst-case currency loss; `build_signal` rejects setups below the R threshold.

### Quickstart

```bash
# Optional: install dev tooling (the library itself needs nothing)
pip install -e ".[dev]"

# Run the tests
python -m pytest -q

# Full workup on the (simulated) synthetic feed
python -m atlas.cli analyze AAPL

# A risk-defined trade plan
python -m atlas.cli signal AAPL --entry 100 --stop 95 --targets 110,120

# An EMA-cross backtest (note the small-sample noise warning)
python -m atlas.cli backtest AAPL --fast 20 --slow 50

# Screen a universe with transparent criteria
python -m atlas.cli screen AAPL,MSFT,NVDA,AMD --above-ema50 --limit 5

# Optimize a portfolio (min-variance) vs a benchmark
python -m atlas.cli portfolio AAPL,MSFT,NVDA --objective min_variance --benchmark SPY
```

### Real market data

Data sources plug into the same `DataProvider` seam:

```bash
# 1. Alpha Vantage — real data with a free API key (recommended for live use)
set ALPHAVANTAGE_API_KEY=YOURKEY        # Windows (cmd);  export on macOS/Linux
python -m atlas.cli analyze AAPL --alpha-vantage
python -m atlas.cli analyze AAPL --alpha-vantage --api-key YOURKEY   # or pass inline

# 2. Your own CSV files (headers auto-detected; see below)
python -m atlas.cli analyze AAPL --csv ./mydata
python -m atlas.cli backtest AAPL --csv ./mydata --lookback 600   # long history = real backtests

# 3. Synthetic (default) — deterministic, seeded, always flagged simulated
python -m atlas.cli analyze AAPL
```

```python
from atlas import ToolRegistry, AlphaVantageProvider
from atlas.analysis import analyze

report = analyze("AAPL", registry=ToolRegistry(AlphaVantageProvider(api_key="YOURKEY")))
```

**Alpha Vantage.** Get a free key (instant, no card) at
<https://www.alphavantage.co/support/#api-key>. Supports daily / weekly / monthly
and intraday (`1m`–`1h`). Two free-tier limits to know:
- **~25 requests/day** — suits single-symbol lookups (`analyze`, `signal`,
  `score`) more than wide screens or multi-symbol portfolios (one request per
  symbol).
- **Daily history is capped at ~100 bars** on the free tier (`outputsize=full`
  is premium-only). That's ample for the indicators, patterns, and regime logic;
  only very long lookbacks (e.g. EMA200) are affected. Pass `--premium` (or
  `AlphaVantageProvider(premium=True)`) with a premium key to unlock full history.

Alpha Vantage returns JSON on errors/rate-limits even in CSV mode — the provider
detects those and raises a clear message instead of mis-parsing.

**Fundamentals & sentiment** (fills the last two sub-scores). Alpha Vantage's
free tier also serves company fundamentals (`OVERVIEW`) and news sentiment
(`NEWS_SENTIMENT`). Opt in per call (each is an extra API request against the
~25/day cap):

```bash
python -m atlas.cli analyze MSFT --alpha-vantage --fundamentals --sentiment
```

The `fundamental` sub-score blends profit margin, ROE, revenue/earnings growth,
P/E, and PEG (each scored 0–100, averaged over those present, with attribution).
The `sentiment` sub-score aggregates recent articles' ticker-specific sentiment,
relevance-weighted. Both stay `null` — never fabricated — when the data is
absent, and the `notes` field says why.

**Stooq (`--stooq`).** Was free and no-key, but Stooq has since put a JavaScript
bot-verification wall in front of its CSV endpoint, so a plain HTTP client can no
longer fetch it; `StooqProvider` detects that page and says so. Still usable if
you download CSVs through a browser and feed them via `--csv`.

**CSV files (`--csv`).** Point `--csv` at a directory of files. The parser
**auto-detects the column layout**, so files exported from common sources load
without editing:

- Native `ts,open,high,low,close,volume`
- Stooq `Date,Open,High,Low,Close,Volume`
- Alpha Vantage `timestamp,open,high,low,close,volume` (newest-first is fine)
- Yahoo `Date,...,Adj Close,Volume` (uses `Adj Close` if no plain close)

Date formats (ISO, `YYYY-MM-DD`, `MM/DD/YYYY`, etc.), newest- or oldest-first
ordering, and a missing volume column are all handled; sentinel rows (`N/D`) are
skipped, not guessed. Files are found by `<SYMBOL>_<TIMEFRAME>.csv` or simply
`<SYMBOL>.csv`. **This is the way to get full-history real backtests for free:**
download a ticker's complete history from a browser (which passes any anti-bot
wall), drop the file in a folder, and run `--csv`.

**Blocked networks.** If your environment restricts outbound HTTPS (egress
policy, firewall), the live providers surface the blocked-host error cleanly —
fall back to `--csv`.

```python
from atlas import analyze, ToolRegistry, SyntheticProvider

report = analyze("AAPL", registry=ToolRegistry(SyntheticProvider(seed=7)))
print(report["regime"], report["atlas_score"], report["score_label"])
```

Calibration tracking — the Appendix B differentiator — logs each signal's stated
confidence and, once resolved, measures whether those probabilities are honest:

```python
from atlas import CalibrationLog

log = CalibrationLog("calibration.json")            # persists to disk
log.log_signal("AAPL", "long", confidence=72, created="2026-08-14", signal_id="a1")
# ...later, once the trade resolves...
log.resolve("a1", "win", realized_r=2.1)
print(log.metrics())   # reliability buckets, Brier score, expected calibration error
```

Point the CLI at real data with `--csv <dir>`, using files named
`<SYMBOL>_<TIMEFRAME>.csv` with header `ts,open,high,low,close,volume`.

## How to deploy

1. Provide the tools above through your LLM's function-calling interface.
2. Copy the contents of [`prompts/atlas-system-prompt.md`](prompts/atlas-system-prompt.md)
   (everything between the `=== SYSTEM PROMPT BEGINS ===` / `ENDS ===` markers)
   into the system/developer role. Do **not** send the operator notes from
   `docs/atlas-spec.md` to the model.
3. Interact using the command modes (Section 16): `analyze <symbol>`,
   `signal <symbol>`, `score <symbol>`, `scan <natural language>`,
   `backtest <rules>`, `portfolio <goal/constraints>`, `rebalance`,
   `alert <condition>`, `explain <prior output>`, `watch <list>`.

## Where the real edge comes from

A system prompt gives you the **best combined feature set and the most
disciplined, transparent reasoning** of the competing tools. It does **not**
manufacture alpha. Genuine competitiveness still requires:

- quality real-time and historical **data feeds**;
- a real **indicator / pattern / backtest compute layer** behind the Section 3 tools;
- honest **cost and slippage** modeling;
- **calibration tracking** that logs every signal's stated confidence against its
  realized outcome, so the agent's probabilities become trustworthy over time.

Wire those in, and the differentiator is the *combination + transparency +
discipline* — never a promise that any prompt beats the market. See
[Appendix B](docs/atlas-spec.md#appendix-b--where-the-real-edge-has-to-come-from-candor).

## License

Released under the [MIT License](LICENSE).
