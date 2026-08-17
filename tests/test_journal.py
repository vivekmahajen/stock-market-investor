"""Tests for the signal journal & calibration wiring (Group J, Appendix B)."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.journal import SignalJournal, _outcome
from atlas.types import OHLCV, Bar


def _series(rows, start_day=0):
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=start_day)
    return OHLCV.from_bars("AAA", "1d", [Bar(t0 + timedelta(days=i), *rows[i]) for i in range(len(rows))])


def _sig(direction="long", entry=100, stop=95, target=110, confidence=70, asof="2020-01-01T00:00:00+00:00"):
    return {"symbol": "AAA", "direction": direction, "entry": entry, "stop": stop,
            "targets": [target], "confidence": confidence, "asof": asof}


# --- outcome logic -------------------------------------------------------
def test_outcome_long_win():
    # price rises to target before hitting stop
    rows = [(100, 105, 99, 104, 1e3), (104, 111, 103, 110, 1e3)]
    o, r = _outcome(_series(rows), 0, 2, entry=100, stop=95, target=110, direction="long")
    assert o == "win" and r == pytest.approx(2.0)  # reward 10 / risk 5


def test_outcome_long_loss():
    rows = [(100, 101, 94, 96, 1e3)]  # low 94 <= stop 95
    o, r = _outcome(_series(rows), 0, 1, 100, 95, 110, "long")
    assert o == "loss" and r == -1.0


def test_outcome_unresolved():
    rows = [(100, 101, 99, 100, 1e3)]  # neither hit
    o, r = _outcome(_series(rows), 0, 1, 100, 95, 110, "long")
    assert o is None


def test_outcome_short_win():
    rows = [(100, 101, 89, 90, 1e3)]  # low 89 <= target 90 (short)
    o, r = _outcome(_series(rows), 0, 1, entry=100, stop=105, target=90, direction="short")
    assert o == "win" and r == pytest.approx(2.0)


def test_outcome_same_bar_stop_first():
    rows = [(100, 111, 94, 100, 1e3)]  # touches both target(110) and stop(95)
    o, r = _outcome(_series(rows), 0, 1, 100, 95, 110, "long")
    assert o == "loss"  # conservative


# --- journal record/resolve ----------------------------------------------
def test_record_ignores_flat_and_incomplete():
    j = SignalJournal()
    assert j.record({"symbol": "AAA", "direction": "flat"}) is None
    assert j.record({"symbol": "AAA", "direction": "long", "entry": 100, "stop": None,
                     "targets": [110]}) is None


def test_record_and_resolve_win(tmp_path):
    path = str(tmp_path / "j.json")
    j = SignalJournal(path)
    j.record(_sig(confidence=80))
    # Forward data (dated after the signal) that reaches the target.
    rows = [(100, 105, 99, 104, 1e3), (104, 112, 103, 111, 1e3)]
    series = _series(rows, start_day=5)  # bars after 2020-01-01
    resolved = j.resolve("AAA", series)
    assert len(resolved) == 1 and resolved[0]["outcome"] == "win"
    m = j.metrics()
    assert m["resolved"] == 1 and m["win_rate_pct"] == 100.0


def test_resolve_only_uses_forward_bars(tmp_path):
    path = str(tmp_path / "j.json")
    j = SignalJournal(path)
    j.record(_sig(asof="2020-02-01T00:00:00+00:00"))
    # Series entirely BEFORE the signal date -> must not resolve.
    rows = [(100, 112, 90, 100, 1e3)] * 5
    series = _series(rows, start_day=0)  # Jan, before Feb signal
    assert j.resolve("AAA", series) == []


def test_persistence_and_metrics(tmp_path):
    path = str(tmp_path / "j.json")
    j = SignalJournal(path)
    j.record(_sig(confidence=90))
    rows = [(100, 112, 99, 111, 1e3)]
    j.resolve("AAA", _series(rows, start_day=5))
    # Reload from disk; the resolved outcome persists.
    j2 = SignalJournal(path)
    m = j2.metrics()
    assert m["resolved"] == 1
    assert "brier_score" in m and "expected_calibration_error" in m


def test_calibration_metrics_shape(tmp_path):
    path = str(tmp_path / "j.json")
    j = SignalJournal(path)
    # Log several signals at 70% confidence, resolve a 70/30 win/loss mix.
    for k in range(10):
        j.log.log_signal("AAA", "long", 70.0, created="2020-01-01", signal_id=f"s{k}")
    for k in range(7):
        j.log.resolve(f"s{k}", "win", 2.0)
    for k in range(7, 10):
        j.log.resolve(f"s{k}", "loss", -1.0)
    m = j.metrics()
    assert m["resolved"] == 10 and m["win_rate_pct"] == 70.0
    assert m["expected_calibration_error"] < 0.05  # 70% stated ≈ 70% realized
