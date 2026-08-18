"""Tests for the new CLI command modes (Group H, §16)."""
import json

import pytest

from atlas.cli import main
from atlas.portfolio import rebalance_plan


def _run(argv, capsys):
    rc = main(argv)
    out = capsys.readouterr().out
    return rc, out


def test_score_command(capsys):
    rc, out = _run(["score", "AAA", "--seed", "3"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["symbol"] == "AAA" and "atlas_score" in d and "subscores" in d
    assert "levels" not in d  # score is a trimmed view


def test_seasonality_command(capsys):
    rc, out = _run(["seasonality", "AAA", "--seed", "3", "--lookback", "500"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["granularity"] == "month" and "buckets" in d


def test_watch_command_ranked(capsys):
    rc, out = _run(["watch", "AAA,BBB,CCC", "--seed", "3"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["count"] == 3
    scores = [r["atlas_score"] for r in d["results"] if "atlas_score" in r]
    assert scores == sorted(scores, reverse=True)


def test_explain_command_text(capsys):
    rc, out = _run(["explain", "AAA", "--seed", "3", "--format", "text"], capsys)
    assert rc == 0
    assert "AAA" in out and "ATLAS" in out


def test_rebalance_command(capsys):
    rc, out = _run(["rebalance", "AAA,BBB,CCC", "--current", "0.6,0.2,0.2", "--seed", "3"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert "trades" in d and "turnover" in d and "target_weights" in d


def test_rebalance_weight_mismatch(capsys):
    rc, out = _run(["rebalance", "AAA,BBB", "--current", "0.5,0.3,0.2", "--seed", "3"], capsys)
    d = json.loads(out)
    assert "error" in d


def test_alert_add_list_remove(tmp_path, capsys):
    store = str(tmp_path / "al.json")
    rc, out = _run(["alert", "add", "AAA", "--kind", "price_above", "--value", "50", "--store", store], capsys)
    assert rc == 0 and "id" in json.loads(out)
    rc, out = _run(["alert", "list", "--store", store], capsys)
    alerts = json.loads(out)["alerts"]
    assert len(alerts) == 1
    aid = alerts[0]["id"]
    rc, out = _run(["alert", "remove", "--id", aid, "--store", store], capsys)
    assert json.loads(out)["removed"] is True


def test_alert_check_runs(tmp_path, capsys):
    store = str(tmp_path / "al.json")
    _run(["alert", "add", "AAA", "--kind", "price_above", "--value", "1", "--store", store], capsys)
    rc, out = _run(["alert", "check", "--seed", "3", "--store", store], capsys)
    assert rc == 0
    assert "triggered" in json.loads(out)


# --- rebalance_plan unit --------------------------------------------------
def test_rebalance_plan_drift_band():
    plan = rebalance_plan({"A": 0.6, "B": 0.4}, {"A": 0.5, "B": 0.5}, drift_band=0.05)
    assert plan["needs_rebalance"] is True
    a = next(t for t in plan["trades"] if t["symbol"] == "A")
    assert a["action"] == "trim"
    b = next(t for t in plan["trades"] if t["symbol"] == "B")
    assert b["action"] == "buy"


def test_rebalance_plan_within_band_holds():
    plan = rebalance_plan({"A": 0.51, "B": 0.49}, {"A": 0.5, "B": 0.5}, drift_band=0.05)
    assert plan["needs_rebalance"] is False
    assert all(t["action"] == "hold" for t in plan["trades"])


def test_rebalance_plan_exit_and_capital():
    plan = rebalance_plan({"A": 0.5, "B": 0.5}, {"A": 1.0}, drift_band=0.05, capital=100_000)
    b = next(t for t in plan["trades"] if t["symbol"] == "B")
    assert b["action"] == "exit"
    assert b["amount"] == -50000.0


# --- daily report / forecast / prediction store (§20-22) -----------------
def test_forecast_command_json(capsys):
    rc, out = _run(["forecast", "AAA", "--seed", "3", "--lookback", "600"], capsys)
    assert rc == 0
    d = json.loads(out)
    assert d["symbol"] == "AAA" and d["horizon_days"] == 30
    assert d["interval_80"]["low"] < d["forecast_price"] < d["interval_80"]["high"]
    assert d["skill"]["samples"] > 0


def test_forecast_command_text_shows_interval_and_skill(capsys):
    rc, out = _run(["forecast", "AAA", "--seed", "3", "--lookback", "600",
                    "--format", "text"], capsys)
    assert rc == 0
    assert "80% interval" in out and "MEASURED SKILL" in out


def test_forecast_command_no_skill_flag(capsys):
    rc, out = _run(["forecast", "AAA", "--seed", "3", "--no-skill"], capsys)
    assert rc == 0 and "skill" not in json.loads(out)


def test_forecast_compare_ranks_methods(capsys):
    rc, out = _run(["forecast", "AAA", "--seed", "3", "--compare", "--format", "text"], capsys)
    assert rc == 0
    assert "FORECAST METHOD COMPARISON" in out
    for m in ("naive", "drift", "blend"):
        assert m in out


def test_daily_command_text(tmp_path, capsys):
    db = str(tmp_path / "d.db")
    rc, out = _run(["daily", "--symbols", "AAA,BBB", "--seed", "3", "--db", db,
                    "--no-skill", "--format", "text"], capsys)
    assert rc == 0
    assert "ATLAS DAILY FORECAST" in out and "AAA" in out and "BBB" in out
    assert "not financial advice" in out


def test_daily_command_writes_a_report_file(tmp_path, capsys):
    db, path = str(tmp_path / "d.db"), str(tmp_path / "report.md")
    rc, _ = _run(["daily", "--symbols", "AAA", "--seed", "3", "--db", db,
                  "--no-skill", "--render", "markdown", "--out", path], capsys)
    assert rc == 0
    assert open(path).read().startswith("# ATLAS Daily Forecast")


def test_daily_command_no_store(tmp_path, capsys):
    import os
    db = str(tmp_path / "none.db")
    rc, out = _run(["daily", "--symbols", "AAA", "--seed", "3", "--db", db,
                    "--no-store", "--no-skill"], capsys)
    assert rc == 0 and not os.path.exists(db)
    assert "run_id" not in json.loads(out)


def test_predictions_lifecycle(tmp_path, capsys):
    db = str(tmp_path / "p.db")
    _run(["daily", "--symbols", "AAA,BBB", "--seed", "3", "--db", db, "--no-skill"], capsys)

    rc, out = _run(["predictions", "runs", "--db", db], capsys)
    assert rc == 0 and len(json.loads(out)["runs"]) == 1

    rc, out = _run(["predictions", "list", "--db", db], capsys)
    assert json.loads(out)["count"] == 2

    rc, out = _run(["predictions", "accuracy", "--db", db], capsys)
    assert json.loads(out)["overall"]["resolved"] == 0

    rc, out = _run(["predictions", "resolve", "--db", db, "--seed", "3",
                    "--asof", "2099-01-01", "--lookback", "900"], capsys)
    assert json.loads(out)["resolved"] == 2

    rc, out = _run(["predictions", "accuracy", "--db", db], capsys)
    assert json.loads(out)["overall"]["resolved"] == 2

    rc, out = _run(["predictions", "report", "--db", db, "--render", "markdown"], capsys)
    assert out.startswith("# ATLAS Daily Forecast")

    rc, out = _run(["predictions", "export", "--db", db], capsys)
    assert "forecast_price" in out.splitlines()[0]

    rc, out = _run(["predictions", "stats", "--db", db], capsys)
    assert json.loads(out)["outcomes"] == 2


def test_predictions_report_on_an_empty_store(tmp_path, capsys):
    rc, out = _run(["predictions", "report", "--db", str(tmp_path / "empty.db")], capsys)
    assert rc == 0 and "no stored runs" in out
