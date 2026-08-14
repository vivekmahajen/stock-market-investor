"""Tests for the risk / position-sizing discipline layer (Section 9)."""
import pytest

from atlas import risk


def test_size_position_basic():
    # 100k account, 1% risk = $1000. Entry 100, stop 95 -> $5/share -> 200 shares.
    ps = risk.size_position(100_000, entry=100, stop=95, direction="long", risk_pct=0.01)
    assert ps.units == 200
    assert ps.risk_amount == pytest.approx(1000.0)
    assert ps.worst_case_loss == pytest.approx(1000.0)


def test_size_position_caps_risk():
    ps = risk.size_position(100_000, entry=100, stop=95, risk_pct=0.10, max_risk_pct=0.02)
    # Risk clamped to 2%.
    assert ps.risk_pct == 0.02
    assert any("exceeds cap" in w for w in ps.warnings)


def test_size_position_wrong_side_stop_raises():
    with pytest.raises(ValueError):
        risk.size_position(100_000, entry=100, stop=105, direction="long")


def test_size_position_short():
    ps = risk.size_position(100_000, entry=100, stop=105, direction="short", risk_pct=0.01)
    assert ps.units == 200
    assert ps.risk_per_unit == pytest.approx(5.0)


def test_size_position_with_multiplier():
    # Futures-style multiplier of 50: risk per unit scales.
    ps = risk.size_position(100_000, entry=100, stop=99, risk_pct=0.01, multiplier=50)
    # $1000 risk / ($1 * 50) = 20 contracts.
    assert ps.units == 20


def test_r_multiple():
    assert risk.r_multiple(100, 95, 110, "long") == pytest.approx(2.0)
    assert risk.r_multiple(100, 105, 90, "short") == pytest.approx(2.0)


def test_meets_r_threshold():
    assert risk.meets_r_threshold(100, 95, 110, threshold=1.5) is True
    assert risk.meets_r_threshold(100, 95, 102, threshold=1.5) is False


def test_portfolio_heat_flags_concentration():
    res = risk.portfolio_heat([2000, 2000, 3000], 100_000)
    assert res["heat_pct"] == pytest.approx(0.07)
    assert res["warnings"]


def test_fractional_kelly_capped():
    res = risk.fractional_kelly(0.6, 2.0, fraction=0.9)
    assert res["applied_fraction"] == 0.5  # never exceeds half
    assert res["kelly_fraction"] >= 0


def test_fractional_kelly_negative_edge_is_zero():
    res = risk.fractional_kelly(0.3, 1.0)
    assert res["kelly_fraction"] == 0.0
