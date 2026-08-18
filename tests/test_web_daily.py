"""Tests for the dashboard's daily-report and prediction-store endpoints (no sockets)."""
import json
import os

import pytest

from atlas.web import (DASHBOARD_HTML, DOC_ROUTES, JSON_ROUTES, _db_path,
                       accuracy_endpoint, export_csv_endpoint, forecast_endpoint,
                       predictions_endpoint, render_report_endpoint,
                       resolve_endpoint, run_daily_endpoint, runs_endpoint,
                       stored_report_endpoint)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Run each test in its own directory so the store file is isolated."""
    monkeypatch.chdir(tmp_path)
    return "test_store.db"


def _run(db, **kw):
    params = {"symbols": "AAA,BBB", "source": "synthetic", "db": db,
              "lookback": "400", "skill": "0"}
    params.update(kw)
    return run_daily_endpoint(params)


# --- db path guard -------------------------------------------------------
def test_db_path_defaults_and_adds_the_suffix():
    assert _db_path({}) == "atlas_predictions.db"
    assert _db_path({"db": "mine"}) == "mine.db"
    assert _db_path({"db": ":memory:"}) == ":memory:"


@pytest.mark.parametrize("bad", ["../secrets.db", "/etc/passwd", "sub/dir.db", ".hidden"])
def test_db_path_refuses_anything_path_like(bad):
    with pytest.raises(ValueError):
        _db_path({"db": bad})


def test_endpoint_turns_a_bad_db_into_a_400():
    status, out = runs_endpoint({"db": "../nope"})
    assert status == 400 and "error" in out


# --- the daily run -------------------------------------------------------
def test_run_daily_endpoint_returns_rows(db):
    status, out = _run(db)
    assert status == 200
    assert len(out["rows"]) == 2
    assert out["run_id"] == 1
    assert os.path.exists(db)


def test_run_daily_endpoint_is_json_serialisable(db):
    _, out = _run(db)
    json.dumps(out, default=str)


def test_run_daily_endpoint_can_skip_persistence(db):
    status, out = _run(db, persist="0")
    assert status == 200 and "run_id" not in out
    assert not os.path.exists(db)


def test_run_daily_endpoint_defaults_to_the_nasdaq_ten(db):
    _, out = _run(db, symbols="", lookback="250")
    assert out["universe"] == "nasdaq10" and len(out["rows"]) == 10


def test_run_daily_endpoint_reports_a_bad_method(db):
    _, out = _run(db, method="crystal_ball")
    assert all("error" in r for r in out["rows"])


# --- reading the store ---------------------------------------------------
def test_runs_endpoint_lists_the_run(db):
    _run(db)
    status, out = runs_endpoint({"db": db})
    assert status == 200 and len(out["runs"]) == 1
    assert out["stats"]["predictions"] == 2


def test_predictions_endpoint_returns_stored_rows(db):
    _run(db)
    status, out = predictions_endpoint({"db": db})
    assert status == 200 and out["count"] == 2
    assert {r["symbol"] for r in out["rows"]} == {"AAA", "BBB"}


def test_predictions_endpoint_filters_by_symbol_and_open_state(db):
    _run(db)
    _, out = predictions_endpoint({"db": db, "symbol": "aaa"})
    assert out["count"] == 1 and out["rows"][0]["symbol"] == "AAA"
    _, open_only = predictions_endpoint({"db": db, "resolved": "0"})
    assert open_only["count"] == 2


def test_stored_report_endpoint_404s_before_any_run(db):
    status, out = stored_report_endpoint({"db": db})
    assert status == 404 and "error" in out


def test_stored_report_endpoint_regenerates_the_run(db):
    _run(db)
    status, out = stored_report_endpoint({"db": db})
    assert status == 200 and out["run_id"] == 1 and len(out["rows"]) == 2


def test_accuracy_endpoint_is_honest_before_resolution(db):
    _run(db)
    status, out = accuracy_endpoint({"db": db})
    assert status == 200
    assert out["overall"]["resolved"] == 0 and out["by_symbol"] == []


def test_resolve_endpoint_scores_elapsed_predictions(db):
    _run(db)
    status, out = resolve_endpoint({"db": db, "source": "synthetic",
                                    "asof": "2099-01-01", "lookback": "900"})
    assert status == 200 and out["resolved"] == 2
    assert out["accuracy"]["resolved"] == 2
    _, acc = accuracy_endpoint({"db": db})
    assert len(acc["by_symbol"]) == 2


# --- single-symbol forecast ---------------------------------------------
def test_forecast_endpoint_requires_a_symbol():
    status, out = forecast_endpoint({"source": "synthetic"})
    assert status == 400 and "error" in out


def test_forecast_endpoint_returns_the_distribution():
    status, out = forecast_endpoint({"symbol": "aaa", "source": "synthetic", "lookback": "600"})
    assert status == 200 and out["symbol"] == "AAA"
    assert out["interval_80"]["low"] < out["forecast_price"] < out["interval_80"]["high"]
    assert out["skill"]["samples"] > 0


def test_forecast_endpoint_can_skip_the_skill_check():
    _, out = forecast_endpoint({"symbol": "AAA", "source": "synthetic", "skill": "0"})
    assert "skill" not in out


# --- documents -----------------------------------------------------------
def test_render_endpoint_returns_html(db):
    _run(db)
    status, body, mime = render_report_endpoint({"db": db, "fmt": "html"})
    assert status == 200 and mime.startswith("text/html")
    assert body.decode().startswith("<!doctype html>")


def test_render_endpoint_stores_what_it_rendered(db):
    from atlas.store import PredictionStore
    _run(db)
    render_report_endpoint({"db": db, "fmt": "markdown"})
    with PredictionStore(db) as store:
        reports = store.reports()
    assert len(reports) == 1 and reports[0]["format"] == "markdown"


def test_render_endpoint_404s_with_an_empty_store(db):
    status, body, mime = render_report_endpoint({"db": db})
    assert status == 404 and mime.startswith("text/plain")


def test_export_endpoint_returns_csv(db):
    _run(db)
    status, body, mime = export_csv_endpoint({"db": db})
    assert status == 200 and mime.startswith("text/csv")
    lines = body.decode().strip().splitlines()
    assert len(lines) == 3 and "forecast_price" in lines[0]


# --- routing & page ------------------------------------------------------
def test_every_new_route_is_registered():
    for path in ("/api/daily/run", "/api/daily/runs", "/api/daily/report",
                 "/api/daily/predictions", "/api/daily/accuracy",
                 "/api/daily/resolve", "/api/forecast"):
        assert path in JSON_ROUTES
    assert "/api/daily/render" in DOC_ROUTES and "/api/daily/export" in DOC_ROUTES


def test_dashboard_page_ships_the_new_tabs_and_views():
    for marker in ("tabDaily", "tabStore", "dailyView", "storeView",
                   "runDaily", "renderStore", "/api/daily/run", "/api/forecast"):
        assert marker in DASHBOARD_HTML


def test_dashboard_page_keeps_the_original_tabs():
    for marker in ("tabChart", "tabScan", "tabBt", "tabAnalysis"):
        assert marker in DASHBOARD_HTML
