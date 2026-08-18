"""Tests for the SQLite prediction store (Section 22)."""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.store import PredictionStore, _close_on_or_before
from atlas.types import OHLCV, Bar


@pytest.fixture()
def store():
    s = PredictionStore(":memory:")
    yield s
    s.close()


def _row(symbol="AAA", last=100.0, fc=105.0, target="2020-02-01", **kw):
    row = {
        "symbol": symbol, "rank": 1, "asof": "2020-01-02T00:00:00+00:00",
        "target_date": target, "horizon_days": 30, "last_close": last,
        "forecast_price": fc, "expected_price": fc * 1.001,
        "forecast_return_pct": (fc / last - 1) * 100,
        "lo80": fc * 0.92, "hi80": fc * 1.08, "lo95": fc * 0.86, "hi95": fc * 1.14,
        "interval_80_width_pct": 16.0, "prob_up": 0.55, "method": "drift",
        "model_version": "forecast-1.0", "atlas_score": 61.0, "score_label": "hold",
        "regime": "range", "simulated": True, "warnings": ["a warning"],
    }
    row.update(kw)
    return row


def _run(store, **kw):
    kw.setdefault("universe", "test")
    kw.setdefault("horizon_days", 30)
    kw.setdefault("method", "drift")
    kw.setdefault("model_version", "forecast-1.0")
    return store.record_run(**kw)


def _series(closes, start="2020-01-01"):
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    bars = [Bar(t0 + timedelta(days=i), c, c * 1.01, c * 0.99, c, 1e6)
            for i, c in enumerate(closes)]
    return OHLCV.from_bars("AAA", "1d", bars)


# --- schema & lifecycle --------------------------------------------------
def test_fresh_store_is_empty_but_valid(store):
    s = store.stats()
    assert s["runs"] == 0 and s["predictions"] == 0 and s["schema_version"] == 1


def test_store_works_as_a_context_manager(tmp_path):
    path = str(tmp_path / "x.db")
    with PredictionStore(path) as s:
        _run(s)
    with PredictionStore(path) as s:  # reopened, data survived
        assert s.stats()["runs"] == 1


def test_future_schema_version_is_refused(tmp_path):
    path = str(tmp_path / "future.db")
    s = PredictionStore(path)
    s.conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    s.conn.commit()
    s.close()
    with pytest.raises(RuntimeError, match="newer ATLAS schema"):
        PredictionStore(path)


# --- writes --------------------------------------------------------------
def test_record_run_and_prediction(store):
    rid = _run(store, symbol_count=1, simulated=True, notes=["hello"])
    pid = store.record_prediction(rid, _row())
    assert pid > 0
    rows = store.predictions(run_id=rid)
    assert len(rows) == 1 and rows[0]["symbol"] == "AAA"
    assert rows[0]["simulated"] is True
    assert rows[0]["warnings"] == ["a warning"]
    assert store.run(rid)["notes"] == ["hello"]


def test_rerunning_a_symbol_in_one_run_replaces_rather_than_duplicates(store):
    rid = _run(store)
    store.record_prediction(rid, _row(fc=105.0))
    store.record_prediction(rid, _row(fc=111.0))
    rows = store.predictions(run_id=rid)
    assert len(rows) == 1 and rows[0]["forecast_price"] == 111.0


def test_unknown_keys_are_ignored_and_missing_ones_are_null(store):
    rid = _run(store)
    store.record_prediction(rid, _row(nonsense="ignore me", atlas_score=None))
    row = store.predictions(run_id=rid)[0]
    assert "nonsense" not in row and row["atlas_score"] is None


def test_latest_run_prefers_the_newest(store):
    _run(store, run_date="2020-01-01")
    newer = _run(store, run_date="2020-06-01")
    assert store.latest_run()["id"] == newer


# --- resolution ----------------------------------------------------------
def test_resolve_scores_a_prediction(store):
    rid = _run(store)
    pid = store.record_prediction(rid, _row(last=100.0, fc=105.0))
    out = store.resolve(pid, actual_price=104.0)
    assert out["error_pct"] == pytest.approx(abs(105 - 104) / 104 * 100, abs=1e-4)
    assert out["direction_correct"] is True      # both above 100
    assert out["within_80"] is True
    assert out["beat_naive"] is True             # |105-104| < |100-104|


def test_resolve_marks_a_wrong_direction(store):
    rid = _run(store)
    pid = store.record_prediction(rid, _row(last=100.0, fc=105.0))
    out = store.resolve(pid, actual_price=95.0)
    assert out["direction_correct"] is False
    assert out["beat_naive"] is False


def test_resolve_flags_an_outcome_outside_the_bands(store):
    rid = _run(store)
    pid = store.record_prediction(rid, _row(last=100.0, fc=105.0))
    out = store.resolve(pid, actual_price=300.0)
    assert out["within_80"] is False and out["within_95"] is False


def test_resolve_rejects_a_non_positive_price(store):
    rid = _run(store)
    pid = store.record_prediction(rid, _row())
    with pytest.raises(ValueError):
        store.resolve(pid, actual_price=0)


def test_resolve_rejects_an_unknown_prediction(store):
    with pytest.raises(KeyError):
        store.resolve(999, actual_price=10)


def test_due_predictions_only_returns_elapsed_and_unresolved(store):
    rid = _run(store)
    past = store.record_prediction(rid, _row(symbol="OLD", target="2020-02-01"))
    store.record_prediction(rid, _row(symbol="NEW", target="2099-01-01"))
    assert [p["symbol"] for p in store.due_predictions("2020-06-01")] == ["OLD"]
    store.resolve(past, 101.0)
    assert store.due_predictions("2020-06-01") == []


def test_resolved_filter_splits_open_from_closed(store):
    rid = _run(store)
    pid = store.record_prediction(rid, _row(symbol="A"))
    store.record_prediction(rid, _row(symbol="B"))
    store.resolve(pid, 101.0)
    assert [r["symbol"] for r in store.predictions(resolved=True)] == ["A"]
    assert [r["symbol"] for r in store.predictions(resolved=False)] == ["B"]


# --- resolving against real bars ----------------------------------------
def test_close_on_or_before_picks_the_last_bar_not_a_later_one():
    s = _series([100, 101, 102, 103, 104])  # 2020-01-01 .. 2020-01-05
    price, ts = _close_on_or_before(s, "2020-01-03")
    assert price == 102 and ts.startswith("2020-01-03")


def test_close_on_or_before_refuses_when_the_series_ends_early():
    s = _series([100, 101])
    assert _close_on_or_before(s, "2020-06-01") == (None, None)


def test_resolve_due_uses_the_registry(store):
    from atlas import SyntheticProvider, ToolRegistry
    reg = ToolRegistry(SyntheticProvider(seed=5))
    fetched = reg.get_ohlcv("AAA", "1d", 300)
    series = fetched["_series"]
    target = series.ts[100].date().isoformat()
    rid = _run(store)
    store.record_prediction(rid, _row(symbol="AAA", target=target,
                                      last=series.close[70], fc=series.close[70] * 1.02))
    out = store.resolve_due(reg, asof="2099-01-01", lookback=300)
    assert out["due"] == 1 and out["resolved"] == 1
    assert out["results"][0]["actual_price"] == pytest.approx(series.close[100], rel=1e-6)


def test_resolve_due_skips_a_symbol_with_no_data(store, tmp_path):
    from atlas import CSVProvider, ToolRegistry
    reg = ToolRegistry(CSVProvider(str(tmp_path)))
    rid = _run(store)
    store.record_prediction(rid, _row(symbol="NOPE", target="2020-02-01"))
    out = store.resolve_due(reg, asof="2099-01-01")
    assert out["resolved"] == 0 and out["skipped"]


# --- aggregates ----------------------------------------------------------
def test_accuracy_is_unknown_before_anything_resolves(store):
    rid = _run(store)
    store.record_prediction(rid, _row())
    acc = store.accuracy()
    assert acc["resolved"] == 0 and acc["open"] == 1
    assert "unknown" in acc["note"]


def test_accuracy_refuses_to_call_a_tiny_sample_a_hit_rate(store):
    rid = _run(store)
    pid = store.record_prediction(rid, _row())
    store.resolve(pid, 104.0)
    acc = store.accuracy()
    assert acc["resolved"] == 1
    assert "not yet evidence" in acc["note"]


def test_accuracy_computes_skill_against_the_naive_baseline(store):
    rid = _run(store)
    for i in range(12):
        pid = store.record_prediction(rid, _row(symbol=f"S{i}", last=100.0, fc=105.0))
        store.resolve(pid, 104.0)  # forecast beats "no change" every time
    acc = store.accuracy()
    assert acc["resolved"] == 12
    assert acc["skill_vs_naive"] > 0
    assert acc["beat_naive_rate_pct"] == 100.0
    assert "below" in acc["note"]


def test_accuracy_can_filter_by_symbol(store):
    rid = _run(store)
    a = store.record_prediction(rid, _row(symbol="AAA", fc=105.0))
    b = store.record_prediction(rid, _row(symbol="BBB", fc=140.0))
    store.resolve(a, 104.0)
    store.resolve(b, 104.0)
    assert store.accuracy(symbol="AAA")["mape_pct"] < store.accuracy(symbol="BBB")["mape_pct"]


def test_leaderboard_ranks_by_error(store):
    rid = _run(store)
    good = store.record_prediction(rid, _row(symbol="GOOD", fc=104.0))
    bad = store.record_prediction(rid, _row(symbol="BAD", fc=150.0))
    store.resolve(good, 104.0)
    store.resolve(bad, 104.0)
    board = store.leaderboard()
    assert [r["symbol"] for r in board] == ["GOOD", "BAD"]


def test_leaderboard_excludes_unresolved(store):
    rid = _run(store)
    store.record_prediction(rid, _row(symbol="OPEN"))
    assert store.leaderboard() == []


# --- export & reports ----------------------------------------------------
def test_export_csv_has_a_header_and_one_row_per_prediction(store):
    rid = _run(store)
    store.record_prediction(rid, _row(symbol="AAA"))
    store.record_prediction(rid, _row(symbol="BBB"))
    lines = store.export_csv().strip().splitlines()
    assert len(lines) == 3
    assert "symbol" in lines[0] and "forecast_price" in lines[0]


def test_export_csv_is_empty_when_nothing_is_stored(store):
    assert store.export_csv() == ""


def test_reports_are_kept_with_their_run(store):
    rid = _run(store)
    store.record_report(rid, "markdown", "# hello", title="t")
    reports = store.reports(run_id=rid)
    assert len(reports) == 1 and reports[0]["content"] == "# hello"
