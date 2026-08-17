"""Tests for portfolio depth (Group F, §11)."""
import pytest

from atlas.data import SyntheticProvider
from atlas.portfolio import (benchmark_comparison, periodic_suggestions,
                             position_roles, tax_aware_notes)


def _universe(n=200, seeds=("AAA", "BBB", "CCC")):
    p = SyntheticProvider(seed=3)
    return {s: p.get_ohlcv(s, "1d", n) for s in seeds}


def _bench(n=200):
    return SyntheticProvider(seed=9).get_ohlcv("SPY", "1d", n)


def test_position_roles_assigns_valid_roles():
    series = _universe()
    weights = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}
    roles = position_roles(series, weights, benchmark=_bench())
    assert set(roles) == set(weights)
    for r in roles.values():
        assert r["role"] in ("core", "satellite", "hedge")
        assert "annual_vol_pct" in r and "beta" in r


def test_benchmark_comparison_fields_and_verdict():
    series = _universe()
    weights = {"AAA": 1 / 3, "BBB": 1 / 3, "CCC": 1 / 3}
    cmp = benchmark_comparison(series, weights, _bench())
    assert "portfolio" in cmp and "benchmark" in cmp
    assert "sharpe" in cmp["portfolio"] and "max_drawdown_pct" in cmp["benchmark"]
    assert "tracking_error_pct" in cmp and "information_ratio" in cmp
    assert "beat" in cmp["verdict"] or "does not" in cmp["verdict"]


def test_periodic_suggestions_structure():
    series = _universe()
    current = {"AAA": 0.6, "BBB": 0.2, "CCC": 0.2}
    sug = periodic_suggestions(series, current, objective="min_variance")
    assert "target_weights" in sug and "rebalance" in sug and "roles" in sug
    assert "suggestions" in sug and "tax" in sug
    for s in sug["suggestions"]:
        assert s["action"] in ("buy", "trim", "exit")
        assert "reason" in s


def test_tax_aware_notes_without_lots():
    trades = [{"symbol": "A", "action": "trim"}, {"symbol": "B", "action": "hold"},
              {"symbol": "C", "action": "exit"}]
    tx = tax_aware_notes(trades)
    assert set(tx["taxable_sells"]) == {"A", "C"}
    assert any("taxable events" in n for n in tx["notes"])


def test_tax_aware_notes_with_lots():
    trades = [{"symbol": "A", "action": "exit"}]
    tx = tax_aware_notes(trades, lots={"A": {"holding_days": 400}})
    assert any("long-term" in n for n in tx["notes"])
    tx2 = tax_aware_notes(trades, lots={"A": {"holding_days": 100}})
    assert any("short-term" in n for n in tx2["notes"])


def test_registry_portfolio_includes_roles_and_comparison():
    from atlas.tools import ToolRegistry
    reg = ToolRegistry(SyntheticProvider(seed=3))
    out = reg.optimize_portfolio(["AAA", "BBB", "CCC"], objective="min_variance", benchmark="SPY")
    assert "roles" in out and "benchmark_comparison" in out
