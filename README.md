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
