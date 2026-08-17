"""Tests for scoring depth (Group I, §10)."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.scoring import (atlas_score, probabilistic_framing,
                           score_forward_study, what_would_change)
from atlas.types import OHLCV, Bar


def _series(closes):
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = [Bar(t0 + timedelta(days=i), closes[i], closes[i] * 1.01, closes[i] * 0.99, closes[i], 1e6)
            for i in range(len(closes))]
    return OHLCV.from_bars("T", "1d", bars)


# --- what_would_change ---------------------------------------------------
def test_what_would_change_upgrade_options():
    subs = {"technical": 50, "risk": 50}  # -> hold-ish
    res = what_would_change(subs)
    assert res["current_label"] in ("hold", "reduce", "accumulate")
    assert "to_upgrade" in res
    for o in res["to_upgrade"]["options"]:
        assert o["raise_by"] > 0 and o["to"] <= 100.5


def test_what_would_change_biggest_drag():
    subs = {"technical": 90, "fundamental": 20, "risk": 80}
    res = what_would_change(subs)
    assert res["biggest_drag"] == "fundamental"


def test_what_would_change_upgrade_math():
    # Single factor -> norm weight 1.0, so raise_by == points to threshold.
    subs = {"technical": 70}
    res = what_would_change(subs)  # score 70 -> accumulate; next up 75 (buy)
    up = res["to_upgrade"]
    assert up["target_label"] == "buy"
    opt = up["options"][0]
    assert opt["raise_by"] == pytest.approx(5.0, abs=0.2)


def test_what_would_change_empty():
    assert "note" in what_would_change({"technical": None})


# --- score study & framing ----------------------------------------------
def test_score_forward_study_bands():
    closes = [100 + i + 5 * ((-1) ** (i // 10)) for i in range(200)]
    study = score_forward_study(_series(closes), forward=10, step=3)
    if study is not None:
        assert study["forward_bars"] == 10
        for row in study["bands"]:
            assert 0 <= row["pct_positive"] <= 100
            assert row["samples"] >= 1


def test_score_forward_study_too_short():
    assert score_forward_study(_series([100 + i for i in range(50)])) is None


def test_probabilistic_framing_sentence():
    closes = [100 * (1.003 ** i) for i in range(220)]
    pf = probabilistic_framing(_series(closes), current_score=80, forward=10, step=3)
    if pf is not None and "band" in pf:
        assert "In-sample" in pf["framing"]
        assert "sample=" in pf["framing"]


def test_probabilistic_framing_beat_benchmark_metric():
    closes = [100 * (1.004 ** i) for i in range(220)]
    bench = _series([100 * (1.001 ** i) for i in range(220)])
    pf = probabilistic_framing(_series(closes), 80, benchmark=bench, forward=10, step=3)
    if pf is not None:
        assert pf["study"]["metric"] == "beat benchmark"


# --- integration ---------------------------------------------------------
def test_analyze_includes_score_dynamics():
    from atlas import ToolRegistry, SyntheticProvider
    from atlas.analysis import analyze
    out = analyze("AAA", registry=ToolRegistry(SyntheticProvider(seed=3)), lookback=300)
    assert "score_dynamics" in out
    assert out["score_dynamics"]["current_label"] == out["score_label"]
    assert out["score_probabilistic"] is None  # study off by default


def test_analyze_with_score_study_on():
    from atlas import ToolRegistry, SyntheticProvider
    from atlas.analysis import analyze
    out = analyze("AAA", registry=ToolRegistry(SyntheticProvider(seed=3)), lookback=300,
                  with_score_study=True)
    assert out["score_probabilistic"] is not None
