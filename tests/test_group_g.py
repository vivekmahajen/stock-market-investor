"""Tests for §3 tools (Group G): options chain, paper trade, calendar, MTF."""
import json

import pytest

from atlas.analysis import multi_timeframe
from atlas.data import AlphaVantageProvider, SyntheticProvider
from atlas.options import build_chain
from atlas.paper import PaperBroker
from atlas.tools import ToolRegistry


# --- options chain -------------------------------------------------------
def test_build_chain_structure():
    ch = build_chain(100, [30, 60], sigma=0.25, n_strikes=3)
    assert ch["generated"] is True and "not live" in ch["note"]
    assert len(ch["expiries"]) == 2
    strikes = ch["expiries"][0]["strikes"]
    assert len(strikes) == 7  # 3 each side + ATM
    for s in strikes:
        assert s["call"]["price"] >= 0 and s["put"]["price"] >= 0
        assert set(s["call"]["greeks"]) == {"delta", "gamma", "theta", "vega", "rho"}


def test_chain_atm_call_delta_near_half():
    ch = build_chain(100, [365], sigma=0.2, n_strikes=1)
    atm = [s for s in ch["expiries"][0]["strikes"] if s["strike"] == 100][0]
    assert 0.45 < atm["call"]["greeks"]["delta"] < 0.65


def test_registry_options_chain():
    reg = ToolRegistry(SyntheticProvider(seed=3))
    ch = reg.get_options_chain("AAA", expiries_days=(30,))
    assert ch["symbol"] == "AAA" and ch["expiries"]
    assert "realized volatility" in ch["sigma_source"]


# --- paper broker --------------------------------------------------------
def test_paper_buy_then_sell_realizes_pnl():
    b = PaperBroker(starting_cash=100_000)
    b.submit("AAPL", "buy", 100, 100.0)
    assert b.positions["AAPL"].qty == 100
    assert b.cash == pytest.approx(90_000)
    fill = b.submit("AAPL", "sell", 100, 110.0)
    assert fill["realized_pnl"] == pytest.approx(1000.0)
    assert "AAPL" not in b.positions
    assert b.realized_pnl == pytest.approx(1000.0)


def test_paper_average_cost_on_add():
    b = PaperBroker()
    b.submit("X", "buy", 10, 100)
    b.submit("X", "buy", 10, 120)
    assert b.positions["X"].avg_price == pytest.approx(110.0)
    assert b.positions["X"].qty == 20


def test_paper_partial_sell():
    b = PaperBroker()
    b.submit("X", "buy", 100, 50)
    b.submit("X", "sell", 40, 60)
    assert b.positions["X"].qty == 60
    assert b.realized_pnl == pytest.approx(40 * 10)


def test_paper_short_and_cover():
    b = PaperBroker()
    b.submit("X", "sell", 10, 100)          # open short
    assert b.positions["X"].qty == -10
    b.submit("X", "buy", 10, 90)            # cover for +100
    assert "X" not in b.positions
    assert b.realized_pnl == pytest.approx(100.0)


def test_paper_flip_long_to_short():
    b = PaperBroker()
    b.submit("X", "buy", 10, 100)
    b.submit("X", "sell", 15, 110)          # close 10 (+100), open short 5 @110
    assert b.positions["X"].qty == -5
    assert b.positions["X"].avg_price == pytest.approx(110.0)
    assert b.realized_pnl == pytest.approx(100.0)


def test_paper_persistence(tmp_path):
    path = str(tmp_path / "book.json")
    b = PaperBroker(path=path)
    b.submit("X", "buy", 5, 100)
    b2 = PaperBroker(path=path)
    assert b2.positions["X"].qty == 5


def test_paper_invalid_side():
    with pytest.raises(ValueError):
        PaperBroker().submit("X", "hold", 1, 1)


def test_registry_paper_trade():
    reg = ToolRegistry(SyntheticProvider())
    res = reg.paper_trade({"symbol": "AAA", "side": "buy", "qty": 10, "price": 100})
    assert res["fill"]["symbol"] == "AAA"
    assert res["account"]["positions"]["AAA"]["qty"] == 10


# --- calendar aggregation ------------------------------------------------
def test_calendar_aggregates_with_injected_av():
    earnings_csv = ("symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
                    "AAA,AAA Inc,2026-09-01,2026-06-30,3.1,USD\n")
    dividends = json.dumps({"symbol": "AAA", "data": [{"ex_dividend_date": "2026-08-20", "amount": "0.5"}]})
    splits = json.dumps({"symbol": "AAA", "data": [{"effective_date": "2026-07-01", "split_factor": "2.0"}]})

    def fetch(url):
        if "EARNINGS_CALENDAR" in url:
            return earnings_csv
        if "DIVIDENDS" in url:
            return dividends
        if "SPLITS" in url:
            return splits
        return ""

    reg = ToolRegistry(AlphaVantageProvider(api_key="K", fetch=fetch))
    cal = reg.get_calendar("AAA")
    assert len(cal["earnings"]) == 1
    assert cal["dividends"][0]["amount"] == "0.5"
    assert cal["splits"][0]["split_factor"] == "2.0"


def test_calendar_unsupported_provider():
    reg = ToolRegistry(SyntheticProvider())
    cal = reg.get_calendar("AAA")
    assert cal["errors"]  # synthetic has no earnings/dividends/splits feeds


# --- multi-timeframe -----------------------------------------------------
def test_multi_timeframe_alignment():
    reg = ToolRegistry(SyntheticProvider(seed=3))
    out = multi_timeframe("AAA", registry=reg, timeframes=("1d", "1w"), lookback=300)
    assert len(out["timeframes"]) == 2
    assert "alignment" in out
    for f in out["timeframes"]:
        assert "regime" in f or "error" in f
