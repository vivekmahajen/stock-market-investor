# ATLAS Web App — TrendSpider-style front-end

Building an interactive web application on top of the ATLAS engine, in reviewable
steps. Run it with `python -m atlas.web` (or `atlas serve`) → http://127.0.0.1:8787.
Tabs: Chart · Scanner · Backtester · Analysis · Daily Report · Predictions.

The engine and all analytics already exist and are tested; this effort adds the
**interactive product UI** that TrendSpider-style sites provide. Local dev server
only — not hardened for public exposure.

Legend: `[ ]` planned · `[~]` partial · `[x]` done.

- [x] **1. Interactive chart** — from-scratch canvas candlestick chart with zoom /
      pan / crosshair, timeframe (1D/1W/1M) + data-source controls, and computed
      overlays drawn on the chart: candles, EMA20/50, Bollinger, VWAP, S/R levels,
      auto trendlines, Fibonacci retracements, bull/bear pattern markers, volume.
      Tabs: Chart | Analysis. API: `/api/chart`.
- [x] **2. Scanner** — visual screener: filter a universe (above-EMA50, RSI band,
      min rel-vol, min 3m return), run `run_screen`, results grid ranked by
      composite score with metrics + liquidity flags; click a row to open the
      chart. API: `/api/scan`.
- [x] **3. Backtester** — EMA-cross strategy with params + costs; verdict banner,
      metrics grid, **trade markers on the price chart** (entry triangles + win/loss
      lines to exits), **equity curve**, and an optional robustness check
      (split / walk-forward / sensitivity / sub-periods). API: `/api/backtest`.
- [x] **4. Daily Report** — run the forecast report over a universe (NASDAQ top 10
      by default, or custom symbols) straight from the browser: summary cards, a
      forecast table with a per-symbol distribution bar (median forecast, today's
      close, the 80% band inside the 95% range), and a click-through detail panel
      showing the model behind any row — volatility, drift, shrinkage, and the
      walk-forward skill statistics. API: `/api/daily/run`, `/api/forecast`.
- [x] **5. Predictions store** — past runs, open vs. resolved predictions with the
      realised price marked on the same bar, realised accuracy (MAPE, skill vs a
      random walk, interval coverage, directional hit rate), the per-symbol
      leaderboard, a **Resolve due** button, and one-click export of the rendered
      report (HTML) or the raw table (CSV). APIs: `/api/daily/runs`,
      `/api/daily/predictions`, `/api/daily/accuracy`, `/api/daily/resolve`,
      `/api/daily/report`, `/api/daily/render`, `/api/daily/export`.
- [ ] **6. Alerts** — create / list / check alerts from the UI (dynamic conditions),
      backed by the alert store.
- [ ] **7. Watchlist** — multi-symbol watchlist scored and ranked (from `watch`),
      click through to the chart.
- [ ] **8. Polish** — chart label de-cluttering, drawing tools, saved layouts,
      multi-pane, options-chain view, a forecast fan overlaid on the price chart.

## Known limitations (data-source, not UI)
- No real-time streaming (providers are request/response; free tiers are EOD).
- Live options IV / open interest and a macro/FOMC calendar need paid feeds; the
  chart uses computed overlays and the options view is model-generated.
