"""Tests for the universe module and the daily forecast report (Sections 20-22)."""
import json

import pytest

from atlas import CSVProvider, SyntheticProvider, ToolRegistry
from atlas.daily import (render_daily, render_daily_html, render_daily_markdown,
                         render_daily_text, report_from_store, run_daily)
from atlas.store import PredictionStore
from atlas.universe import (NASDAQ_TOP10, SNAPSHOT_ASOF, UnknownUniverse,
                            list_universes, rank_by_market_cap, resolve_universe,
                            static_universe)

SYMS = ["AAA", "BBB", "CCC"]


@pytest.fixture()
def reg():
    return ToolRegistry(SyntheticProvider(seed=17))


@pytest.fixture()
def store():
    s = PredictionStore(":memory:")
    yield s
    s.close()


# --- universe ------------------------------------------------------------
def test_static_universe_returns_ten_nasdaq_names():
    syms = static_universe("nasdaq10")
    assert len(syms) == 10 and syms == NASDAQ_TOP10
    assert all(s.isupper() for s in syms)


def test_static_universe_respects_a_limit():
    assert static_universe("nasdaq10", limit=3) == NASDAQ_TOP10[:3]


def test_static_universe_rejects_an_unknown_name():
    with pytest.raises(UnknownUniverse):
        static_universe("ftse5000")


def test_list_universes_reports_sizes():
    assert list_universes()["nasdaq10"] == 10


def test_resolve_universe_labels_the_snapshot_and_warns_it_is_dated():
    out = resolve_universe("nasdaq10")
    assert out["ranking_source"] == "static-snapshot"
    assert out["ranking_asof"] == SNAPSHOT_ASOF
    assert any("static snapshot" in n for n in out["notes"])


def test_resolve_universe_falls_back_when_no_fundamentals_feed(reg):
    out = resolve_universe("nasdaq10", registry=reg, refresh=True)
    assert out["ranking_source"] == "static-snapshot"
    assert any("fell back" in n or "no registry" in n for n in out["notes"])


def test_resolve_universe_reranks_from_a_market_cap_feed():
    class _Reg:
        def get_fundamentals(self, symbol):
            caps = {"AAA": 3e12, "BBB": 1e12, "CCC": 2e12}
            if symbol not in caps:
                return {"error": "no coverage"}
            return {"overview": {"MarketCapitalization": str(caps[symbol])}}

    out = resolve_universe("nasdaq10", registry=_Reg(), refresh=True, limit=3,
                           pool=["AAA", "BBB", "CCC", "DDD"])
    assert out["ranking_source"] == "live-market-cap"
    assert out["symbols"] == ["AAA", "CCC", "BBB"]
    assert any("no market-cap data" in n for n in out["notes"])


def test_rank_by_market_cap_excludes_symbols_it_could_not_price():
    class _Reg:
        def get_fundamentals(self, symbol):
            return ({"overview": {"MarketCapitalization": "100"}} if symbol == "AAA"
                    else {"overview": {"MarketCapitalization": "None"}})

    out = rank_by_market_cap(["AAA", "BBB"], _Reg(), top=5)
    assert out["symbols"] == ["AAA"] and out["covered"] == 1 and out["errors"]


# --- the run -------------------------------------------------------------
def test_run_daily_produces_a_row_per_symbol(reg, store):
    rep = run_daily(registry=reg, symbols=SYMS, store=store, with_skill=False, lookback=400)
    assert len(rep["rows"]) == 3
    assert {r["symbol"] for r in rep["rows"]} == set(SYMS)
    assert rep["summary"]["count"] == 3


def test_run_daily_rows_carry_forecast_and_analysis(reg, store):
    rep = run_daily(registry=reg, symbols=["AAA"], store=store, with_skill=False, lookback=400)
    row = rep["rows"][0]
    for key in ("last_close", "forecast_price", "lo80", "hi80", "lo95", "hi95",
                "prob_up", "atlas_score", "regime", "target_date"):
        assert row[key] is not None
    assert row["lo95"] < row["lo80"] < row["hi80"] < row["hi95"]


def test_run_daily_defaults_to_the_nasdaq_top_ten(reg, store):
    rep = run_daily(registry=reg, store=store, with_skill=False, lookback=250)
    assert rep["universe"] == "nasdaq10"
    assert [r["symbol"] for r in rep["rows"]] == NASDAQ_TOP10
    assert rep["ranking_source"] == "static-snapshot"


def test_run_daily_flags_simulated_data(reg, store):
    rep = run_daily(registry=reg, symbols=["AAA"], store=store, with_skill=False, lookback=400)
    assert rep["data_is_simulated"] is True
    assert any("SIMULATED" in n for n in rep["notes"])


def test_run_daily_lists_a_failed_symbol_without_dropping_it(tmp_path, store):
    reg = ToolRegistry(CSVProvider(str(tmp_path)))
    rep = run_daily(registry=reg, symbols=["NOPE"], store=store, with_skill=False)
    assert len(rep["rows"]) == 1 and "error" in rep["rows"][0]
    assert any("produced no forecast" in n for n in rep["notes"])
    assert rep["summary"]["count"] == 0


def test_run_daily_warns_when_the_data_is_stale(reg, store):
    rep = run_daily(registry=reg, symbols=["AAA"], store=store, with_skill=False,
                    lookback=400, run_date="2099-01-01")
    assert any("STALE DATA" in n for n in rep["notes"])


def test_run_daily_persists_run_and_predictions(reg, store):
    rep = run_daily(registry=reg, symbols=SYMS, store=store, with_skill=False, lookback=400)
    assert rep["run_id"] == store.latest_run()["id"]
    assert store.stats()["predictions"] == 3
    assert store.stats()["runs"] == 1


def test_run_daily_can_skip_persistence(reg, store):
    rep = run_daily(registry=reg, symbols=["AAA"], persist=False, with_skill=False, lookback=400)
    assert "run_id" not in rep
    assert store.stats()["predictions"] == 0


def test_run_daily_skill_check_populates_measured_error(reg, store):
    rep = run_daily(registry=reg, symbols=["AAA"], store=store, with_skill=True, lookback=600)
    row = rep["rows"][0]
    assert row["backtest_samples"] > 0
    assert row["skill_vs_naive"] is not None
    assert row["skill_verdict"]


def test_summary_says_skill_was_unmeasured_when_it_was(reg, store):
    rep = run_daily(registry=reg, symbols=SYMS, store=store, with_skill=False, lookback=400)
    assert rep["summary"]["skill_measured"] == 0
    assert "not measured" in rep["summary"]["skill_note"]


def test_run_daily_is_json_serialisable(reg, store):
    rep = run_daily(registry=reg, symbols=SYMS, store=store, with_skill=False, lookback=400)
    json.dumps(rep, default=str)  # must not raise


# --- regenerating from the store ----------------------------------------
def test_report_from_store_errors_before_any_run(store):
    assert "error" in report_from_store(store)


def test_report_from_store_rebuilds_the_same_numbers(reg, store):
    rep = run_daily(registry=reg, symbols=SYMS, store=store, with_skill=False, lookback=400)
    again = report_from_store(store)
    assert again["run_id"] == rep["run_id"]
    assert len(again["rows"]) == 3
    by_symbol = {r["symbol"]: r for r in again["rows"]}
    for row in rep["rows"]:
        assert by_symbol[row["symbol"]]["forecast_price"] == row["forecast_price"]


def test_report_from_store_shows_outcomes_once_resolved(reg, store):
    rep = run_daily(registry=reg, symbols=["AAA"], store=store, with_skill=False, lookback=300)
    store.resolve_due(reg, asof="2099-01-01", lookback=900)
    again = report_from_store(store, run_id=rep["run_id"])
    assert again["resolved_count"] == 1
    assert again["rows"][0]["actual_price"] is not None
    assert again["accuracy_to_date"]["resolved"] == 1


# --- rendering -----------------------------------------------------------
def test_text_report_contains_the_headline_facts(reg, store):
    rep = run_daily(registry=reg, symbols=SYMS, store=store, with_skill=False, lookback=400)
    text = render_daily_text(rep)
    assert "ATLAS DAILY FORECAST" in text
    assert "SIMULATED DATA" in text
    for s in SYMS:
        assert s in text
    assert "not financial advice" in text


def test_markdown_report_is_a_table_with_every_symbol(reg, store):
    rep = run_daily(registry=reg, symbols=SYMS, store=store, with_skill=False, lookback=400)
    md = render_daily_markdown(rep)
    assert md.startswith("# ATLAS Daily Forecast")
    assert md.count("| **") >= 3
    assert "80% band" in md


def test_html_report_is_self_contained(reg, store):
    rep = run_daily(registry=reg, symbols=SYMS, store=store, with_skill=False, lookback=400)
    html = render_daily_html(rep)
    assert html.startswith("<!doctype html>")
    assert "<style>" in html and "http://" not in html.replace("http://www.w3.org", "")
    assert "SIMULATED DATA" in html


def test_html_report_escapes_symbol_text(store):
    rep = {"report": "x", "run_date": "2020-01-01", "universe": "<script>bad</script>",
           "horizon_days": 30, "method": "drift", "model_version": "v", "rows": [],
           "summary": {}, "notes": [], "generated_at": "now"}
    html = render_daily_html(rep)
    assert "<script>bad</script>" not in html and "&lt;script&gt;" in html


def test_render_daily_dispatches_and_falls_back_to_json(reg, store):
    rep = run_daily(registry=reg, symbols=["AAA"], store=store, with_skill=False, lookback=400)
    assert render_daily(rep, "text").startswith("=")
    assert render_daily(rep, "markdown").startswith("#")
    assert render_daily(rep, "html").startswith("<!doctype")
    assert json.loads(render_daily(rep, "nonsense"))["run_date"] == rep["run_date"]


def test_renderers_handle_an_error_envelope():
    err = {"error": "no stored runs yet"}
    assert "no stored runs yet" in render_daily_text(err)
    assert "no stored runs yet" in render_daily_markdown(err)
    assert "no stored runs yet" in render_daily_html(err)


# --- the tool-registry surface ------------------------------------------
def test_registry_forecast_price_includes_skill(reg):
    out = reg.forecast_price("AAA", horizon_days=30, lookback=600)
    assert out["forecast_price"] > 0
    assert out["skill"]["samples"] > 0
    assert out["simulated"] is True


def test_registry_forecast_price_surfaces_fetch_errors(tmp_path):
    reg = ToolRegistry(CSVProvider(str(tmp_path)))
    assert "error" in reg.forecast_price("NOPE")


def test_registry_get_universe_matches_the_module(reg):
    assert reg.get_universe("nasdaq10")["symbols"] == NASDAQ_TOP10


def test_registry_daily_report_round_trip(reg, tmp_path):
    db = str(tmp_path / "r.db")
    rep = reg.run_daily_report(symbols=["AAA", "BBB"], db_path=db, with_skill=False, lookback=400)
    assert rep["run_id"] == 1
    queried = reg.query_predictions(db_path=db)
    assert queried["count"] == 2
    again = reg.report_from_store(db_path=db, fmt="markdown")
    assert again["rendered"].startswith("# ATLAS Daily Forecast")
    acc = reg.forecast_accuracy(db_path=db)
    assert acc["overall"]["resolved"] == 0
    resolved = reg.resolve_predictions(db_path=db, asof="2099-01-01", lookback=900)
    assert resolved["resolved"] == 2
