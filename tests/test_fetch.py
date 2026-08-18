"""Tests for the fetch-to-CSV-cache workflow (write_ohlcv_csv + `atlas fetch`)."""
from __future__ import annotations

import json
import os

from atlas import cli
from atlas.data import CSVProvider, SyntheticProvider, write_ohlcv_csv


def test_write_ohlcv_csv_roundtrips(tmp_path):
    series = SyntheticProvider().get_ohlcv("AAPL", "1d", 300)
    path = str(tmp_path / "AAPL_1d.csv")
    n = write_ohlcv_csv(series, path)
    assert n == 300
    back = CSVProvider(str(tmp_path)).get_ohlcv("AAPL", "1d", 0)
    assert len(back) == 300
    assert abs(back.close[-1] - series.close[-1]) < 1e-9
    assert back.ts[0].date() == series.ts[0].date()


def test_write_ohlcv_csv_native_header(tmp_path):
    series = SyntheticProvider().get_ohlcv("AAA", "1d", 10)
    path = str(tmp_path / "AAA_1d.csv")
    write_ohlcv_csv(series, path)
    first = open(path, encoding="utf-8").readline().strip()
    assert first == "ts,open,high,low,close,volume"


def test_fetch_cli_writes_cache_then_csv_reads_it(tmp_path, monkeypatch, capsys):
    # Stub the default live source (Yahoo) with the deterministic synthetic feed.
    class FakeYahoo(SyntheticProvider):
        source = "yahoo"

    monkeypatch.setattr(cli, "YahooProvider", FakeYahoo)
    out_dir = str(tmp_path / "cache")
    rc = cli.main(["fetch", "AAPL,MSFT", "--out", out_dir, "--lookback", "400"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "yahoo"
    assert {w["symbol"] for w in payload["written"]} == {"AAPL", "MSFT"}
    assert all(w["bars"] == 400 for w in payload["written"])
    assert os.path.exists(os.path.join(out_dir, "AAPL_1d.csv"))

    # The cached files now feed a real, many-fold skill measurement offline.
    rc2 = cli.main(["forecast", "AAPL", "--compare", "--csv", out_dir])
    assert rc2 == 0
    cmp = json.loads(capsys.readouterr().out)
    assert cmp["skill_measured"] is True
    assert cmp["methods"][0]["skill"]["folds"] > 30  # not noise-dominated


def test_fetch_cli_reports_per_symbol_errors(tmp_path, monkeypatch, capsys):
    class BrokenYahoo(SyntheticProvider):
        source = "yahoo"

        def get_ohlcv(self, symbol, timeframe, lookback):
            if symbol.upper() == "BAD":
                raise RuntimeError("no data for BAD")
            return super().get_ohlcv(symbol, timeframe, lookback)

    monkeypatch.setattr(cli, "YahooProvider", BrokenYahoo)
    rc = cli.main(["fetch", "AAPL,BAD", "--out", str(tmp_path / "c"), "--lookback", "200"])
    assert rc == 0  # partial success still writes what it can
    written = {w["symbol"]: w for w in json.loads(capsys.readouterr().out)["written"]}
    assert "bars" in written["AAPL"]
    assert "error" in written["BAD"] and "no data" in written["BAD"]["error"]
