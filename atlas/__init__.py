"""ATLAS — reference compute layer for the market-intelligence agent.

This package implements the *tools* the ATLAS system prompt calls (Section 3):
indicators, structure detection, pattern recognition, backtesting, seasonality,
risk/position sizing, and the explainable ATLAS Score. It is the honest
"compute layer behind the tools" that the spec's Appendix B names as a real
requirement — deterministic, dependency-free, and never fabricating data.

The system prompt itself lives in ``prompts/atlas-system-prompt.md``.
"""
from __future__ import annotations

from . import indicators, levels, patterns, portfolio, risk, scoring, screen, seasonality
from .alerts import Alert, AlertStore
from .analysis import analyze, build_signal, classify_regime, confluence_score
from .backtest import run_backtest, verdict
from .calibration import CalibrationLog, SignalRecord
from .data import CSVProvider, DataProvider, SyntheticProvider
from .portfolio import optimize_portfolio
from .screen import run_screen
from .tools import ToolRegistry
from .types import OHLCV, Bar, Quote

__version__ = "0.1.0"

__all__ = [
    "indicators",
    "levels",
    "patterns",
    "portfolio",
    "risk",
    "scoring",
    "screen",
    "seasonality",
    "analyze",
    "build_signal",
    "classify_regime",
    "confluence_score",
    "run_backtest",
    "verdict",
    "run_screen",
    "optimize_portfolio",
    "Alert",
    "AlertStore",
    "CalibrationLog",
    "SignalRecord",
    "ToolRegistry",
    "DataProvider",
    "SyntheticProvider",
    "CSVProvider",
    "OHLCV",
    "Bar",
    "Quote",
    "__version__",
]
