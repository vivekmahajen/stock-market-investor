"""Tests for fundamental & sentiment sub-scores and the AV JSON endpoints."""
import json

import pytest

from atlas.analysis import analyze
from atlas.data import AlphaVantageProvider
from atlas.fundamentals import fundamental_subscore, sentiment_subscore
from atlas.tools import ToolRegistry

_STRONG_OVERVIEW = {
    "Symbol": "MSFT", "Name": "Microsoft", "Sector": "TECHNOLOGY",
    "ProfitMargin": "0.36", "ReturnOnEquityTTM": "0.38",
    "QuarterlyRevenueGrowthYOY": "0.18", "QuarterlyEarningsGrowthYOY": "0.20",
    "PERatio": "34.5", "PEGRatio": "2.3",
}

_WEAK_OVERVIEW = {
    "Symbol": "XYZ", "Name": "Weak Co", "Sector": "ENERGY",
    "ProfitMargin": "-0.05", "ReturnOnEquityTTM": "-0.02",
    "QuarterlyRevenueGrowthYOY": "-0.10", "PERatio": "-3", "PEGRatio": "None",
}


def test_fundamental_strong_scores_high():
    r = fundamental_subscore(_STRONG_OVERVIEW)
    assert r is not None
    assert r["score"] > 60
    assert "profit_margin" in r["factors"]
    assert r["name"] == "Microsoft"


def test_fundamental_weak_scores_low():
    r = fundamental_subscore(_WEAK_OVERVIEW)
    assert r is not None
    assert r["score"] < 40


def test_fundamental_missing_values_handled():
    ov = {"Symbol": "X", "ProfitMargin": "None", "PERatio": "-", "PEGRatio": ""}
    # Only sentinels -> fewer than 2 real factors -> None (not fabricated).
    assert fundamental_subscore(ov) is None


def test_fundamental_two_factor_minimum():
    ov = {"Symbol": "X", "ProfitMargin": "0.2", "PERatio": "20"}
    r = fundamental_subscore(ov)
    assert r is not None and 0 <= r["score"] <= 100


def _news(scores_labels):
    feed = []
    for s, lbl, rel in scores_labels:
        feed.append({
            "title": "x", "overall_sentiment_score": s, "overall_sentiment_label": lbl,
            "ticker_sentiment": [{"ticker": "MSFT", "ticker_sentiment_score": str(s), "relevance_score": str(rel)}],
        })
    return {"feed": feed}


def test_sentiment_bullish():
    news = _news([(0.35, "Bullish", 0.9), (0.30, "Somewhat-Bullish", 0.8)])
    r = sentiment_subscore(news, "MSFT")
    assert r is not None
    assert r["score"] > 80
    assert r["articles"] == 2


def test_sentiment_bearish():
    news = _news([(-0.35, "Bearish", 0.9), (-0.25, "Somewhat-Bearish", 0.9)])
    r = sentiment_subscore(news, "MSFT")
    assert r["score"] < 25


def test_sentiment_empty_feed_none():
    assert sentiment_subscore({"feed": []}, "MSFT") is None


def test_sentiment_relevance_weighting():
    # A highly-relevant bearish article should outweigh a barely-relevant bullish one.
    news = {"feed": [
        {"overall_sentiment_score": 0.3, "ticker_sentiment": [{"ticker": "MSFT", "ticker_sentiment_score": "0.3", "relevance_score": "0.05"}]},
        {"overall_sentiment_score": -0.3, "ticker_sentiment": [{"ticker": "MSFT", "ticker_sentiment_score": "-0.3", "relevance_score": "0.95"}]},
    ]}
    r = sentiment_subscore(news, "MSFT")
    assert r["avg_sentiment"] < 0


# --- provider JSON endpoints (offline) -----------------------------------
def test_provider_get_fundamentals():
    p = AlphaVantageProvider(api_key="K", fetch=lambda url: json.dumps(_STRONG_OVERVIEW))
    ov = p.get_fundamentals("MSFT")
    assert ov["Symbol"] == "MSFT"


def test_provider_fundamentals_rate_limit():
    p = AlphaVantageProvider(api_key="K", fetch=lambda url: '{"Information": "25 per day limit"}')
    with pytest.raises(RuntimeError):
        p.get_fundamentals("MSFT")


def test_provider_fundamentals_empty_symbol():
    p = AlphaVantageProvider(api_key="K", fetch=lambda url: "{}")
    with pytest.raises(RuntimeError, match="No fundamentals"):
        p.get_fundamentals("NOPE")


def test_provider_news_sentiment():
    payload = json.dumps(_news([(0.2, "Somewhat-Bullish", 0.7)]))
    p = AlphaVantageProvider(api_key="K", fetch=lambda url: payload)
    news = p.get_news_sentiment("MSFT")
    assert "feed" in news and len(news["feed"]) == 1


# --- end-to-end via analyze ----------------------------------------------
def _ohlcv_csv():
    from datetime import date, timedelta
    rows = ["timestamp,open,high,low,close,volume"]
    price = 100.0
    d = date(2023, 1, 2)
    series = []
    for i in range(130):
        price *= 1.001
        series.append((d + timedelta(days=i), price))
    for dt, px in reversed(series):
        rows.append(f"{dt},{px:.2f},{px*1.01:.2f},{px*0.99:.2f},{px:.2f},1000000")
    return "\n".join(rows) + "\n"


def test_analyze_with_fundamentals_and_sentiment():
    ohlcv = _ohlcv_csv()
    news = json.dumps(_news([(0.3, "Bullish", 0.9)]))
    overview = json.dumps(_STRONG_OVERVIEW)

    def fake_fetch(url):
        if "OVERVIEW" in url:
            return overview
        if "NEWS_SENTIMENT" in url:
            return news
        return ohlcv

    reg = ToolRegistry(AlphaVantageProvider(api_key="K", fetch=fake_fetch))
    out = analyze("MSFT", registry=reg, lookback=130, with_fundamentals=True, with_sentiment=True)
    assert out["subscores"]["fundamental"] is not None
    assert out["subscores"]["sentiment"] is not None
    assert out["fundamentals_detail"]["name"] == "Microsoft"
    assert out["sentiment_detail"]["articles"] == 1
    assert out["data_is_simulated"] is False


def test_analyze_without_flags_stays_null_with_note():
    reg = ToolRegistry(AlphaVantageProvider(api_key="K", fetch=lambda url: _ohlcv_csv()))
    out = analyze("MSFT", registry=reg, lookback=130)
    assert out["subscores"]["fundamental"] is None
    assert out["subscores"]["sentiment"] is None
    assert any("with_fundamentals" in n for n in out["notes"])
