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
from .backtest import run_backtest, verdict
from .robustness import (parameter_sensitivity, sub_period_analysis,
                         train_test_split, walk_forward)
from .calibration import CalibrationLog, SignalRecord
from .events import build_event_risk, parse_earnings_csv
from .fundamentals import fundamental_subscore, sentiment_subscore
from .options import bs_greeks, bs_price, implied_vol, option_analysis
from .chart_patterns import detect_classical
from .patterns import pattern_base_rate
from .data import AlphaVantageProvider, CSVProvider, DataProvider, StooqProvider, SyntheticProvider
from .fibonacci import auto_fibonacci
from .harmonics import detect_harmonics
from .portfolio import (benchmark_comparison, optimize_portfolio,
                        periodic_suggestions, position_roles, rebalance_plan,
                        tax_aware_notes)
from .screen import run_screen
from .tools import ToolRegistry
from .types import OHLCV, Bar, Quote

__version__ = "0.1.0"

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
    "build_event_risk",
    "parse_earnings_csv",
    "bs_price",
    "bs_greeks",
    "implied_vol",
    "option_analysis",
    "Alert",
    "AlertStore",
    "CalibrationLog",
    "SignalRecord",
    "ToolRegistry",
    "DataProvider",
    "SyntheticProvider",
    "CSVProvider",
    "StooqProvider",
    "AlphaVantageProvider",
    "OHLCV",
    "Bar",
    "Quote",
    "__version__",
]
