# ATLAS — Roadmap & Gap Analysis

Coverage of the [ATLAS spec](docs/atlas-spec.md) as of this document. The **core
of every section is built, tested, and running on real data**; this file
enumerates the remaining named features so they can be completed one at a time.

Legend: `[ ]` not started · `[~]` partial · `[x]` done.

---

## A. Indicator library (§4) — `atlas/indicators.py` ✅ DONE
Built: SMA, EMA, WMA, RSI, MACD, ATR, ATR%, Bollinger, Stochastic, ADX/DMI, OBV, ROC, VWAP, relative volume.

- [x] A1. `hma` — Hull moving average
- [x] A2. `vwma` — volume-weighted moving average
- [x] A3. `anchored_vwap` — VWAP from an anchor index
- [x] A4. `supertrend`
- [x] A5. `ichimoku` — tenkan/kijun/senkou A-B/chikou
- [x] A6. `parabolic_sar`
- [x] A7. `linreg_channel` — linear-regression channel
- [x] A8. `stoch_rsi`
- [x] A9. `cci` — Commodity Channel Index
- [x] A10. `williams_r`
- [x] A11. `tsi` — True Strength Index
- [x] A12. `mfi` — Money Flow Index
- [x] A13. `rsi_divergence` — bullish/bearish divergence detection
- [x] A14. `keltner_channels`
- [x] A15. `donchian_channels`
- [x] A16. `historical_volatility`
- [x] A17. `choppiness_index`
- [x] A18. `ad_line` — accumulation/distribution
- [x] A19. `cmf` — Chaikin Money Flow
- [x] A20. `vwap_bands`
- [x] A21. `volume_profile` — POC / value area / HVN-LVN
- [x] A22. `rs_rating` — relative-strength percentile vs benchmark
- [x] A23. `beta` / `correlation` exposed as standalone indicators
- [~] A24. `cumulative_delta` — N/A: requires bid/ask tick data no OHLCV feed provides

## B. Structure detection (§4) — `atlas/levels.py` ✅ DONE
Built: horizontal S/R by touch-count, swing pivots, Fibonacci (in `fibonacci.py`).

- [x] B1. `detect_trendlines` — least-squares support/resistance trendlines with slope, projection, touches
- [x] B2. `detect_channels` — parallel-channel detection from the trendlines
- [x] B3. `pivot_points` — classic / Camarilla / Woodie
- [x] B4. `detect_gaps` — up/down gaps + fill status
- [x] B5. `volume_profile_levels` — POC / value-area / HVN-LVN (wraps the indicator)
- [x] B6. S/R weighting by volume — `Level.volume` + `strength` (touches × volume share)

Surfaced in the analyze envelope (`structure`) and `ToolRegistry.detect_structure`.

## C. Patterns (§5) ✅ DONE
Built candlestick: doji, hammer, engulfing, marubozu, shooting star. Classical: H&S (+inv), double top/bottom, triangles. Harmonic: Gartley, Bat, Butterfly, Crab, Shark.

Candlestick — `atlas/patterns.py`
- [x] C1. `bullish_harami` / `bearish_harami`
- [x] C2. `morning_star` / `evening_star`
- [x] C3. `three_white_soldiers` / `three_black_crows`
- [x] C4. `tweezer_top` / `tweezer_bottom`
- [x] C5. `hanging_man` (trend-context; + `inverted_hammer`)

Classical — `atlas/chart_patterns.py`
- [x] C6. `triple_top` / `triple_bottom`
- [x] C7. `rising_wedge` / `falling_wedge`
- [x] C8. `bull_flag` / `bear_flag`
- [x] C9. `bull_pennant` / `bear_pennant`
- [x] C10. `rectangle`
- [x] C11. `cup_and_handle`
- [x] C12. `rounding_top` / `rounding_bottom`
- [x] C13. `broadening_formation`

Harmonic — `atlas/harmonics.py`
- [x] C14. `cypher`

Base rates — `atlas/patterns.py`
- [x] C15. `pattern_base_rate` — in-sample empirical follow-through study; attached to candlestick patterns in the analyze envelope

## D. Signal enrichment (§6) — `atlas/analysis.py` ✅ DONE
Built: entry/stop/targets/R/position-size, sub-threshold-R rejection, event attachment.

- [x] D1. numeric `confidence` (0–100) with drivers (confluence + technical + pattern alignment − event penalty)
- [x] D2. one-line `thesis` generator
- [x] D3. `biggest_risk` field
- [x] D4. `what_would_make_me_wrong` / invalidation-in-words (auto-derived from stop when manual)
- [x] D5. `catalyst_or_expiry` tied to the event calendar
- [x] D6. `propose_signal(symbol)` — auto-derive direction/entry/stop/targets from structure & Fibonacci; returns `flat` when no clean setup

CLI: `signal <symbol>` now auto-proposes when entry/stop/targets are omitted.

## E. Backtesting robustness (§8) — `atlas/robustness.py` ✅ DONE
Built: next-bar-open engine, costs/slippage, full metrics, small-sample verdict.

- [x] E1. `train_test_split` — in-sample vs out-of-sample, reported separately
- [x] E2. `walk_forward` — anchored walk-forward with per-fold optimization + param stability
- [x] E3. `parameter_sensitivity` — grid over params, coef-of-variation curve-fitting verdict
- [x] E4. `sub_period_analysis` — performance/consistency across contiguous sub-periods

CLI: `backtest --robustness {split|walkforward|sensitivity|subperiods}`.

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
