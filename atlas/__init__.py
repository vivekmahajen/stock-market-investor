"""ATLAS — reference compute layer for the market-intelligence agent.

This package implements the *tools* the ATLAS system prompt calls (Section 3):
indicators, structure detection, pattern recognition, backtesting, seasonality,
risk/position sizing, and the explainable ATLAS Score. It is the honest
"compute layer behind the tools" that the spec's Appendix B names as a real
requirement — deterministic, dependency-free, and never fabricating data.

The system prompt itself lives in ``prompts/atlas-system-prompt.md``.
"""
from __future__ import annotations

from . import (
    chart_patterns,
    fibonacci,
    harmonics,
    indicators,
    levels,
    patterns,
    portfolio,
    risk,
    scoring,
    screen,
    seasonality,
)
from .alerts import Alert, AlertStore
from .analysis import analyze, build_signal, classify_regime, confluence_score, propose_signal
from .daily import (render_daily, render_daily_html, render_daily_markdown,
                    render_daily_text, report_from_store, run_daily)
from .forecast import backtest_forecast, compare_methods, forecast, prob_above
from .store import PredictionStore
from .universe import NASDAQ_TOP10, resolve_universe, static_universe
from .backtest import run_backtest, verdict
from .robustness import (parameter_sensitivity, sub_period_analysis,
                         train_test_split, walk_forward)
from .calibration import CalibrationLog, SignalRecord
from .journal import SignalJournal
from .events import build_event_risk, parse_earnings_csv
from .fundamentals import fundamental_subscore, sentiment_subscore
from .scoring import (probabilistic_framing, score_forward_study,
                      what_would_change)
from .analysis import multi_timeframe
from .options import bs_greeks, bs_price, build_chain, implied_vol, option_analysis
from .paper import PaperBroker
from .chart_patterns import detect_classical
from .patterns import pattern_base_rate
from .data import (AlphaVantageProvider, CSVProvider, DataProvider, StooqProvider,
                   SyntheticProvider, YahooProvider)
from .fibonacci import auto_fibonacci
from .harmonics import detect_harmonics
from .portfolio import (benchmark_comparison, optimize_portfolio,
                        periodic_suggestions, position_roles, rebalance_plan,
                        tax_aware_notes)
from .screen import run_screen
from .tools import ToolRegistry
from .types import OHLCV, Bar, Quote

__version__ = "0.2.0"

__all__ = [
    "chart_patterns",
    "fibonacci",
    "harmonics",
    "indicators",
    "levels",
    "patterns",
    "portfolio",
    "risk",
    "scoring",
    "screen",
    "seasonality",
    "detect_classical",
    "detect_harmonics",
    "auto_fibonacci",
    "analyze",
    "build_signal",
    "propose_signal",
    "classify_regime",
    "confluence_score",
    "run_backtest",
    "verdict",
    "train_test_split",
    "walk_forward",
    "parameter_sensitivity",
    "sub_period_analysis",
    "run_screen",
    "optimize_portfolio",
    "rebalance_plan",
    "position_roles",
    "benchmark_comparison",
    "periodic_suggestions",
    "tax_aware_notes",
    "fundamental_subscore",
    "sentiment_subscore",
    "what_would_change",
    "score_forward_study",
    "probabilistic_framing",
    "build_event_risk",
    "parse_earnings_csv",
    "bs_price",
    "bs_greeks",
    "implied_vol",
    "option_analysis",
    "build_chain",
    "PaperBroker",
    "multi_timeframe",
    "Alert",
    "AlertStore",
    "CalibrationLog",
    "SignalRecord",
    "SignalJournal",
    "forecast",
    "backtest_forecast",
    "compare_methods",
    "prob_above",
    "run_daily",
    "report_from_store",
    "render_daily",
    "render_daily_text",
    "render_daily_markdown",
    "render_daily_html",
    "PredictionStore",
    "resolve_universe",
    "static_universe",
    "NASDAQ_TOP10",
    "ToolRegistry",
    "DataProvider",
    "SyntheticProvider",
    "CSVProvider",
    "StooqProvider",
    "YahooProvider",
    "AlphaVantageProvider",
    "OHLCV",
    "Bar",
    "Quote",
    "__version__",
]
