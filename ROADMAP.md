# ATLAS — Roadmap & Gap Analysis

Coverage of the [ATLAS spec](docs/atlas-spec.md) as of this document. The **core
of every section is built, tested, and running on real data**; this file
enumerates the remaining named features so they can be completed one at a time.

Legend: `[ ]` not started · `[~]` partial · `[x]` done.

---

## A. Indicator library (§4) — `atlas/indicators.py`
Built: SMA, EMA, WMA, RSI, MACD, ATR, ATR%, Bollinger, Stochastic, ADX/DMI, OBV, ROC, VWAP, relative volume.

- [ ] A1. `hma` — Hull moving average
- [ ] A2. `vwma` — volume-weighted moving average
- [ ] A3. `anchored_vwap` — VWAP from an anchor index
- [ ] A4. `supertrend`
- [ ] A5. `ichimoku` — tenkan/kijun/senkou A-B/chikou
- [ ] A6. `parabolic_sar`
- [ ] A7. `linreg_channel` — linear-regression channel
- [ ] A8. `stoch_rsi`
- [ ] A9. `cci` — Commodity Channel Index
- [ ] A10. `williams_r`
- [ ] A11. `tsi` — True Strength Index
- [ ] A12. `mfi` — Money Flow Index
- [ ] A13. `rsi_divergence` — bullish/bearish divergence detection
- [ ] A14. `keltner_channels`
- [ ] A15. `donchian_channels`
- [ ] A16. `historical_volatility`
- [ ] A17. `choppiness_index`
- [ ] A18. `ad_line` — accumulation/distribution
- [ ] A19. `cmf` — Chaikin Money Flow
- [ ] A20. `vwap_bands`
- [ ] A21. `volume_profile` — POC / value area / HVN-LVN
- [ ] A22. `rs_rating` — relative-strength percentile vs benchmark
- [ ] A23. `beta` / `correlation` exposed as standalone indicators (exist in portfolio)
- [~] A24. `cumulative_delta` — needs bid/ask tick data; mark N/A unless a feed provides it

## B. Structure detection (§4) — `atlas/levels.py`
Built: horizontal S/R by touch-count, swing pivots, Fibonacci (in `fibonacci.py`).

- [ ] B1. `detect_trendlines` — dynamic auto-adjusting trendlines
- [ ] B2. `detect_channels` — parallel channel from trendlines
- [ ] B3. `pivot_points` — classic / Camarilla / Woodie
- [ ] B4. `detect_gaps` — up/down gaps + fill status
- [ ] B5. `volume_profile_levels` — POC / value-area / node levels (shares A21)
- [ ] B6. S/R weighting by volume (currently touch-count only)

## C. Patterns (§5)
Built candlestick: doji, hammer, engulfing, marubozu, shooting star. Classical: H&S (+inv), double top/bottom, triangles. Harmonic: Gartley, Bat, Butterfly, Crab, Shark.

Candlestick — `atlas/patterns.py`
- [ ] C1. `harami`
- [ ] C2. `morning_star` / `evening_star`
- [ ] C3. `three_white_soldiers` / `three_black_crows`
- [ ] C4. `tweezers` (top/bottom)
- [ ] C5. `hanging_man` (trend-context distinct from hammer)

Classical — `atlas/chart_patterns.py`
- [ ] C6. `triple_top` / `triple_bottom`
- [ ] C7. `wedge` (rising/falling)
- [ ] C8. `flag`
- [ ] C9. `pennant`
- [ ] C10. `rectangle`
- [ ] C11. `cup_and_handle`
- [ ] C12. `rounding_top` / `rounding_bottom`
- [ ] C13. `broadening_formation`

Harmonic — `atlas/harmonics.py`
- [ ] C14. `cypher`

Base rates — `atlas/patterns.py` / new
- [ ] C15. `pattern_base_rate` — empirical follow-through study over the series (fills the `base_rate: null` fields)

## D. Signal enrichment (§6) — `atlas/analysis.py`
Built: entry/stop/targets/R/position-size, sub-threshold-R rejection, event attachment.

- [ ] D1. numeric `confidence` (0–100) with drivers (from confluence + score + pattern alignment)
- [ ] D2. one-line `setup_thesis` generator
- [ ] D3. `biggest_risk` field
- [ ] D4. `what_would_make_me_wrong` / invalidation-in-words
- [ ] D5. `catalyst_or_expiry` tied to the event calendar
- [ ] D6. `propose_signal(symbol)` — auto-derive entry/stop (structure) + targets (levels/fib) from analysis

## E. Backtesting robustness (§8) — `atlas/backtest.py`
Built: next-bar-open engine, costs/slippage, full metrics, small-sample verdict.

- [ ] E1. `train_test_split` — in-sample vs out-of-sample, reported separately
- [ ] E2. `walk_forward` — rolling-window walk-forward analysis
- [ ] E3. `parameter_sensitivity` — grid over params, report stability/curve-fitting risk
- [ ] E4. `sub_period_analysis` — performance across sub-periods / regimes

## F. Portfolio depth (§11) — `atlas/portfolio.py`
Built: optimizer (equal/inv-vol/min-var/max-sharpe), correlation, beta, stress test.

- [ ] F1. `rebalance_plan` — drift bands / schedule, trades to make, turnover cost
- [ ] F2. `position_roles` — core / satellite / hedge tagging
- [ ] F3. `benchmark_comparison` — vs index or 60/40 (return + risk), not just beta
- [ ] F4. `periodic_suggestions` — what changed, add/trim, why
- [ ] F5. tax-aware notes (optional, needs lot data)

## G. §3 tools
- [ ] G1. `get_options_chain(symbol, expiry?)` — build a chain (strikes around spot × expiries) priced with `options.py` greeks; live feed optional
- [ ] G2. `paper_trade(order)` — simulated fill + a simple position/PnL ledger (never live)
- [ ] G3. `get_calendar` extensions — dividends and splits (AV endpoints); macro/FOMC if a feed exists
- [ ] G4. multi-timeframe fetch/analysis helper (spec: "always fetch multiple timeframes")

## H. Command modes (§16) — `atlas/cli.py`
Built CLI: analyze, signal, backtest, screen, portfolio, option, serve.

- [ ] H1. `score` — dedicated ATLAS-Score command (currently folded into analyze)
- [ ] H2. `rebalance` — portfolio rebalancing suggestions (needs F1)
- [ ] H3. `explain` — deeper rationale on a prior output
- [ ] H4. `watch` — ongoing monitoring / watchlist
- [ ] H5. `alert` — expose create/list/check alerts in the CLI
- [ ] H6. `seasonality` — expose `compute_seasonality` in the CLI

## I. Scoring depth (§10) — `atlas/scoring.py`
Built: five-factor blend, attribution, adjustable weights, label, horizon.

- [ ] I1. probabilistic framing — historical outperformance by score band + regime (needs a score backtest)
- [ ] I2. explicit "what would change the score" output

## J. Calibration wiring (Appendix B)
Built: `CalibrationLog` (Brier, ECE, reliability buckets).

- [ ] J1. signal journal — auto-log every issued signal, resolve outcomes, feed the calibration log so stated confidence becomes auditable over time

---

## Suggested order

1. **A. Indicator library** — foundational; many other features build on it.
2. **E. Backtesting robustness** — §8 explicitly demands out-of-sample / walk-forward.
3. **D. Signal enrichment** — makes signals spec-complete (confidence, thesis, invalidation).
4. **B. Structure detection** — trendlines, pivots, gaps, volume profile.
5. **C. Patterns** — remaining candlestick / classical / harmonic + base rates.
6. **H. Command modes** — expose everything from the CLI.
7. **F. Portfolio depth** — rebalancing, roles, benchmark, suggestions.
8. **G. §3 tools** — options chain, paper_trade, calendar extensions.
9. **I / J. Scoring depth & calibration wiring** — probabilistic framing, signal journal.
